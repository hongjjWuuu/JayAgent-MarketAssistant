import pytest

from app.core.security import AuthContext
from app.domain.models import EvaluationRun, RetrievalProfile
from app.repositories.memory import InMemoryStore
from app.schemas.api import PolicyCreate
from app.services.market_service import MarketAssistantService
from app.services.policy_service import PolicyService
from app.services.query_analyzer import analyze_query
from scripts.seed_week4_hard_negative import build_cases


def admin() -> AuthContext:
    return AuthContext("tenant_demo", "admin_001", "tenant_admin", "req", "admin")


def publish(store: InMemoryStore, scope: str, content: str, acl: dict | None = None) -> None:
    service = MarketAssistantService(store)
    value = service.create_policy(
        admin(),
        PolicyCreate(scope=scope, version="v1", title=scope, content=content, acl=acl or {}),
    )
    service.publish_policy(admin(), value.policy_version_id)


def test_query_analysis_exposes_gate_v3_domain_and_acl_signals():
    value = analyze_query(
        "我是上海金卡会员，限定物流补偿多少钱",
        {"region": "北京", "customer_tier": "普通会员"},
    )
    assert value.domain_intent == "supported"
    assert {"物流", "补偿", "会员"}.issubset(value.business_anchors)
    assert value.explicit_acl_context["region"] == "北京" or value.explicit_acl_context["region"] == "上海"
    assert value.requested_policy_scope is None


def test_hard_negative_dataset_has_deterministic_calibration_holdout_split():
    cases = build_cases()
    assert len(cases) == 60
    assert sum("calibration" in case.tags for case in cases) == 40
    assert sum("holdout" in case.tags for case in cases) == 20
    assert len({case.name for case in cases}) == 60


@pytest.mark.parametrize(
    "query",
    ["帮我写订单系统代码", "写一首关于快递的诗", "退货商品摄影教程", "会员健身计划"],
)
def test_creative_or_unrelated_queries_are_explicitly_unsupported(query: str):
    assert analyze_query(query).domain_intent == "unsupported"


def test_out_of_scope_semantic_similarity_is_never_answered():
    store = InMemoryStore()
    publish(store, "logistics", "物流延误达到48小时可以创建售后工单。")
    decision = PolicyService(store).answerability_gate("tenant_demo", "物流行业股票什么时候上涨")
    assert decision.decision == "handoff"
    assert decision.reason_code == "UNSUPPORTED_SCOPE"
    snapshot = store.get_retrieval_snapshot(decision.retrieval_snapshot_id, "tenant_demo")
    assert snapshot and snapshot.diagnostics["gate_rule_id"] == "GATE-V4-003"


def test_required_facts_cannot_be_combined_across_policy_families():
    store = InMemoryStore()
    publish(store, "returns-duration", "售后退货必须在7天内申请。")
    publish(store, "compensation-amount", "售后补偿金额为50元。")
    decision = PolicyService(store).answerability_gate(
        "tenant_demo", "售后退货多久并且补偿金额多少元"
    )
    assert decision.decision == "clarify"
    assert decision.reason_code == "MISSING_FACT"
    snapshot = store.get_retrieval_snapshot(decision.retrieval_snapshot_id, "tenant_demo")
    assert snapshot
    assert not any(
        item["complete"]
        for item in snapshot.diagnostics["fact_coverage_by_policy_family"].values()
    )


def test_explicit_acl_mismatch_handoffs_instead_of_falling_back():
    store = InMemoryStore()
    publish(
        store,
        "member-exclusive",
        "上海金卡会员专享售后补偿100元。",
        {"region": ["上海"], "customer_tier": ["金卡"]},
    )
    decision = PolicyService(store).answerability_gate(
        "tenant_demo",
        "上海金卡专享售后补偿多少钱",
        filters={"region": "北京", "customer_tier": "普通会员"},
    )
    assert decision.decision == "handoff"
    assert decision.reason_code == "ACL_CONTEXT_MISMATCH"


def test_gate_v3_profile_requires_gold_calibration_and_holdout():
    store = InMemoryStore()
    profile = store.save_retrieval_profile(
        RetrievalProfile(
            retrieval_profile_id="bge-m3-v2",
            tenant_id="tenant_demo",
            provider="openai_compatible",
            embedding_model="BAAI/bge-m3",
            embedding_dimension=1024,
            gate_version="v3",
            normalization_version="query-normalizer-v3",
            status="ready",
            gate_config={"gate_version": "v3"},
            calibration_run_id="calibration",
            indexed_chunks=0,
            total_chunks=0,
        )
    )
    gold = EvaluationRun(
        evaluation_run_id="gold",
        tenant_id="tenant_demo",
        status="completed",
        dataset="week4_gold",
        dataset_version="v3",
        metric_schema_version="v3",
        quality_status="passed",
        summary={
            "false_answer_rate": 0,
            "old_version_hit_rate": 0,
            "tenant_leakage_rate": 0,
            "acl_leakage_rate": 0,
        },
        config_snapshot={"retrieval_profile_id": profile.retrieval_profile_id},
    )
    store.save_evaluation_run(gold)
    with pytest.raises(ValueError, match="immutable"):
        store.activate_retrieval_profile(profile.retrieval_profile_id, "tenant_demo", "gold", "admin")
    holdout = gold.model_copy(
        update={
            "evaluation_run_id": "holdout",
            "dataset": "week4_hard_negative",
            "dataset_version": "v1",
        }
    )
    store.save_evaluation_run(holdout)
    profile.holdout_run_id = holdout.evaluation_run_id
    store.save_retrieval_profile(profile)
    with pytest.raises(ValueError, match="immutable"):
        store.activate_retrieval_profile(
            profile.retrieval_profile_id, "tenant_demo", "gold", "admin"
        )
