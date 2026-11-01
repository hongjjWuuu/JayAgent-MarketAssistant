import pytest
from pydantic import ValidationError

from app.agents.orchestrator import MarketOrchestrator
from app.core.security import AuthContext
from app.domain.models import EvaluationCase, EvaluationRun, RetrievalProfile, RetrievalSnapshot
from app.llm.provider import FakeLlmProvider, GroundedAnswerResult, IntentResult
from app.repositories.memory import InMemoryStore
from app.schemas.api import PolicyCreate
from app.services.evaluation_metrics import evaluate_case
from app.services.market_service import MarketAssistantService
from app.services.policy_service import AnswerabilityDecision, PolicyHit


def admin() -> AuthContext:
    return AuthContext("tenant_demo", "admin_001", "tenant_admin", "req", "admin_001")


def customer() -> AuthContext:
    return AuthContext(
        "tenant_demo", "customer_001", "customer", "req", "customer_001", "customer_001"
    )


def test_policy_family_identity_survives_version_replacement():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    first = service.create_policy(
        admin(),
        PolicyCreate(scope="family", version="v1", title="政策", content="商品签收后七天以内允许申请退货"),
    )
    service.publish_policy(admin(), first.policy_version_id)
    second = service.create_policy(
        admin(),
        PolicyCreate(scope="family", version="v2", title="政策", content="商品签收后十四天以内允许申请退货"),
    )
    service.publish_policy(admin(), second.policy_version_id)
    assert first.policy_id == second.policy_id
    assert first.policy_version_id != second.policy_version_id


def test_policy_version_and_chunk_metrics_are_independent():
    hit = PolicyHit(
        chunk_id="wrong-chunk",
        tenant_id="tenant_demo",
        policy_id="family-1",
        policy_version_id="wrong-version",
        title="政策",
        version="v2",
        scope="returns",
        excerpt="七天",
        rrf_score=0.03,
    )
    decision = AnswerabilityDecision(
        decision="answer",
        reason_code="ANSWERABLE",
        confidence=0.9,
        required_follow_up=[],
        evidence_chunk_ids=[hit.chunk_id],
        retrieval_snapshot_id="snapshot",
        gate_version="v2",
        hits=[hit],
    )
    case = EvaluationCase(
        evaluation_case_id="case",
        tenant_id="tenant_demo",
        dataset="week4_gold",
        dataset_version="v3",
        name="metric split",
        case_type="answerable",
        input_data={"query": "退货"},
        expected={
            "decision": "answer",
            "required_citation_policy_ids": ["family-1"],
            "required_citation_policy_version_ids": ["right-version"],
            "required_citation_chunk_ids": ["right-chunk"],
        },
    )
    snapshot = RetrievalSnapshot(
        retrieval_snapshot_id="snapshot",
        tenant_id="tenant_demo",
        query_redacted="退货",
        query_hash="hash",
        decision="answer",
        reason_code="ANSWERABLE",
        confidence=0.9,
    )
    result = evaluate_case("tenant_demo", "run", case, decision, snapshot)
    assert result.metrics["citation_policy_correct"]
    assert not result.metrics["citation_version_correct"]
    assert not result.metrics["citation_chunk_correct"]


def test_acl_restricted_policy_needs_context():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    draft = service.create_policy(
        admin(),
        PolicyCreate(
            scope="shanghai",
            version="v1",
            title="上海政策",
            content="上海地区商品七天内退货。",
            acl={"region": ["上海"]},
        ),
    )
    service.publish_policy(admin(), draft.policy_version_id)
    assert store.search_policy_chunks(
        "tenant_demo", "退货", [0.0] * 1536, 5, {}, "hash-embedding-v1", 1536
    ) == []


def test_retrieval_profile_activation_requires_passed_evaluation():
    store = InMemoryStore()
    profile = store.save_retrieval_profile(
        RetrievalProfile(
            retrieval_profile_id="quality-profile-v1",
            tenant_id="tenant_demo",
            provider="openai_compatible",
            embedding_model="BAAI/bge-m3",
            embedding_dimension=1024,
        )
    )
    store.reindex_retrieval_profile(profile.retrieval_profile_id, "tenant_demo")
    store.save_evaluation_run(
        EvaluationRun(
            evaluation_run_id="run-passed",
            tenant_id="tenant_demo",
            status="completed",
            dataset_version="v3",
            quality_status="passed",
            metric_schema_version="v3",
                config_snapshot={"retrieval_profile_id": "quality-profile-v1"},
        )
    )
    active = store.activate_retrieval_profile(
        profile.retrieval_profile_id, "tenant_demo", "run-passed", "admin_001"
    )
    assert active and active.status == "active"
    assert sum(item.status == "active" for item in store.list_retrieval_profiles("tenant_demo")) == 1


def test_rejected_bge_v1_can_never_be_reactivated():
    store = InMemoryStore()
    profile = store.save_retrieval_profile(
        RetrievalProfile(
            retrieval_profile_id="bge-m3-v1",
            tenant_id="tenant_demo",
            provider="openai_compatible",
            embedding_model="BAAI/bge-m3",
            embedding_dimension=1024,
            status="rejected",
        )
    )
    store.save_evaluation_run(
        EvaluationRun(
            evaluation_run_id="run-v1",
            tenant_id="tenant_demo",
            status="completed",
            dataset_version="v3",
            quality_status="passed",
            metric_schema_version="v3",
            config_snapshot={"retrieval_profile_id": profile.retrieval_profile_id},
        )
    )
    with pytest.raises(ValueError, match="immutable"):
        store.activate_retrieval_profile(
            profile.retrieval_profile_id, "tenant_demo", "run-v1", "admin_001"
        )
    with pytest.raises(ValueError, match="immutable"):
        store.reindex_retrieval_profile(profile.retrieval_profile_id, "tenant_demo")


class BadCitationProvider(FakeLlmProvider):
    def compose_grounded_answer(self, query, evidence, allowed_citation_ids):
        del query, evidence, allowed_citation_ids
        return GroundedAnswerResult(reply="越权答案", citation_chunk_ids=["not-allowed"])


def test_llm_structured_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        IntentResult.model_validate(
            {"intent": "order", "order_id": "order_1001", "execute_refund": True}
        )


def test_controlled_graph_falls_back_when_model_citation_is_invalid():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    draft = service.create_policy(
        admin(),
        PolicyCreate(
            scope="graph-policy",
            version="v1",
            title="退货",
            content="# 退货期限\n商品签收后七天内可以申请退货。",
        ),
    )
    service.publish_policy(admin(), draft.policy_version_id)
    graph = MarketOrchestrator(store, 0.015, llm=BadCitationProvider())
    result = graph.invoke(
        customer(),
        "退货期限是多久",
        conversation_id="conversation",
        message_id="message",
        task_id="task",
    )
    assert result["kind"] == "policy_answer"
    assert result["reply"] != "越权答案"
    traces = store.list_llm_traces("tenant_demo")
    assert any(trace.call_type == "compose_grounded_answer" and trace.fallback_used for trace in traces)
    assert all("not-allowed" not in trace.allowed_citation_ids for trace in traces)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("查询 order_1001 的物流", "order_fact"),
        ("商品破损，请创建工单", "ticket_requested"),
        ("请转人工客服", "handoff_requested"),
        ("今天天气如何", "supported_scope"),
    ],
)
def test_controlled_graph_routes_without_granting_model_write_authority(message, kind):
    store = InMemoryStore()
    graph = MarketOrchestrator(store, 0.015, llm=FakeLlmProvider())
    result = graph.invoke(customer(), message)
    assert result["kind"] == kind
    if kind == "ticket_requested":
        assert result["preview"]["action_type"] == "create_ticket"
        assert store.tickets == {}
