from app.core.security import AuthContext
from app.domain.models import (
    EvaluationCase,
    EvaluationRun,
    RetrievalSnapshot,
)
from app.repositories.memory import InMemoryStore
from app.schemas.api import PolicyCreate
from app.services.evaluation_metrics import evaluate_case, percentile, quality_failures, summarize
from app.services.market_service import MarketAssistantService
from app.services.policy_service import AnswerabilityDecision, PolicyHit, PolicyService
from app.services.query_analyzer import analyze_query, normalize_query


def admin() -> AuthContext:
    return AuthContext(
        tenant_id="tenant_demo",
        subject="admin_001",
        role="tenant_admin",
        request_id="week41",
        username="admin_001",
    )


def publish(store: InMemoryStore, content: str = "# 退货期限\n商品签收后7天内可以退货。"):
    service = MarketAssistantService(store)
    policy = service.create_policy(
        admin(),
        PolicyCreate(
            scope="week41_returns",
            version="v1",
            title="退货政策",
            content=content,
        ),
    )
    return service.publish_policy(admin(), policy.policy_version_id)


def test_dataset_freeze_prevents_mutation():
    store = InMemoryStore()
    case = EvaluationCase(
        evaluation_case_id="case-1",
        tenant_id="tenant_demo",
        dataset="week4_gold",
        dataset_version="v2",
        name="frozen",
        case_type="answerable",
        input_data={"query": "退货期限"},
        expected={"decision": "answer"},
    )
    assert store.import_evaluation_cases([case]) == 1
    frozen = store.freeze_evaluation_dataset("tenant_demo", "week4_gold", "v2")
    assert frozen and frozen.status == "frozen" and frozen.case_count == 1
    try:
        store.import_evaluation_cases([case])
    except ValueError as error:
        assert str(error) == "DATASET_FROZEN"
    else:
        raise AssertionError("frozen dataset accepted a mutation")


def test_embedding_model_mismatch_blocks_before_search():
    store = InMemoryStore()
    policy = publish(store)
    store.policy_chunks[policy.policy_version_id][0]["embedding_model"] = "other-model"
    decision = PolicyService(store).answerability_gate("tenant_demo", "退货期限")
    assert decision.decision == "handoff"
    assert decision.reason_code == "INDEX_MODEL_MISMATCH"
    snapshot = store.get_retrieval_snapshot(decision.retrieval_snapshot_id, "tenant_demo")
    assert snapshot and snapshot.diagnostics["incompatible_chunk_count"] == 1


def test_query_analyzer_normalizes_and_requires_context():
    analysis = analyze_query("这个商品七天内能不能退？", {})
    assert analysis.question_type == "missing_context"
    assert "product_or_category" in analysis.missing_facts
    assert "duration" in analysis.required_facts
    assert "eligibility" in analysis.required_facts
    assert "7天" in normalize_query("七天")


def test_required_fact_missing_cannot_answer():
    store = InMemoryStore()
    publish(store, "# 退货期限\n退货期限以客服核验为准，符合条件的商品可以申请退货。")
    decision = PolicyService(store).answerability_gate("tenant_demo", "退货期限是多久")
    assert decision.decision == "clarify"
    assert decision.reason_code == "MISSING_FACT"


def test_citation_accuracy_is_independent_from_recall():
    hit = PolicyHit(
        chunk_id="chunk-1",
        tenant_id="tenant_demo",
        policy_version_id="policy-v1",
        title="退货",
        version="v1",
        scope="returns",
        excerpt="7天内退货",
        rrf_score=0.03,
    )
    decision = AnswerabilityDecision(
        decision="answer",
        reason_code="ANSWERABLE",
        confidence=0.9,
        required_follow_up=[],
        evidence_chunk_ids=[],
        retrieval_snapshot_id="snapshot-1",
        gate_version="v2",
        hits=[hit],
    )
    snapshot = RetrievalSnapshot(
        retrieval_snapshot_id="snapshot-1",
        tenant_id="tenant_demo",
        query_redacted="退货期限",
        query_hash="hash",
        decision="answer",
        reason_code="ANSWERABLE",
        confidence=0.9,
    )
    case = EvaluationCase(
        evaluation_case_id="case-1",
        tenant_id="tenant_demo",
        dataset="week4_gold",
        dataset_version="v2",
        name="citation",
        case_type="answerable",
        input_data={"query": "退货期限"},
        expected={
            "decision": "answer",
            "reason_codes": ["ANSWERABLE"],
            "relevant_policy_version_ids": ["policy-v1"],
            "required_citation_policy_version_ids": ["policy-v1"],
        },
    )
    result = evaluate_case("tenant_demo", "run-1", case, decision, snapshot)
    assert result.metrics["recall_at_1"] == 1.0
    assert not result.metrics["citation_version_correct"]
    assert "citation_version_mismatch" in result.details["failure_reasons"]


def test_summary_calculates_security_metrics_and_percentiles():
    assert percentile([1, 2, 3, 100], 0.95) == 100
    hit = PolicyHit(
        chunk_id="forbidden-chunk",
        tenant_id="tenant_other",
        policy_version_id="forbidden-v1",
        title="secret",
        version="v1",
        scope="secret",
        excerpt="私有政策",
        rrf_score=0.03,
    )
    decision = AnswerabilityDecision(
        decision="answer",
        reason_code="ANSWERABLE",
        confidence=0.9,
        required_follow_up=[],
        evidence_chunk_ids=[hit.chunk_id],
        retrieval_snapshot_id="snapshot-2",
        gate_version="v2",
        hits=[hit],
    )
    snapshot = RetrievalSnapshot(
        retrieval_snapshot_id="snapshot-2",
        tenant_id="tenant_demo",
        query_redacted="私有政策",
        query_hash="hash",
        decision="answer",
        reason_code="ANSWERABLE",
        confidence=0.9,
    )
    case = EvaluationCase(
        evaluation_case_id="case-2",
        tenant_id="tenant_demo",
        dataset="week4_gold",
        dataset_version="v2",
        name="leak",
        case_type="cross_tenant",
        input_data={"query": "私有政策"},
        expected={
            "decision": "handoff",
            "reason_codes": ["NO_EVIDENCE"],
            "forbidden_policy_version_ids": ["forbidden-v1"],
        },
    )
    result = evaluate_case("tenant_demo", "run-2", case, decision, snapshot)
    summary = summarize([result], [case])
    assert summary["tenant_leakage_rate"] == 1.0
    assert summary["false_answer_rate"] == 1.0
    assert quality_failures(summary)


def test_compare_rejects_different_dataset_versions():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    for version in ("v1", "v2"):
        store.save_evaluation_run(
            EvaluationRun(
                evaluation_run_id=f"run-{version}",
                tenant_id="tenant_demo",
                dataset="week4_gold",
                dataset_version=version,
                status="completed",
            )
        )
    comparison = service.compare_evaluations(admin(), "run-v1", "run-v2")
    assert not comparison["comparable"]
    assert comparison["reasons"] == ["dataset_or_version_differs"]
