from app.core.security import AuthContext
from app.domain.models import EvaluationCase
from app.repositories.memory import InMemoryStore
from app.runtime.worker import LeaseGuard, TaskWorker
from app.schemas.api import EvaluationRunRequest, PolicyCreate, PolicyUpdate, RetrievalDebugRequest
from app.services.market_service import MarketAssistantService


def admin() -> AuthContext:
    return AuthContext(
        tenant_id="tenant_demo",
        subject="admin_001",
        role="tenant_admin",
        request_id="test",
        username="admin_001",
    )


def published_policy(store: InMemoryStore, *, acl: dict | None = None):
    service = MarketAssistantService(store)
    policy = service.create_policy(
        admin(),
        PolicyCreate(
            scope="returns",
            version="v1",
            title="退货政策",
            content="# 退货期限\n商品签收后七天内可以申请无理由退货。",
            acl=acl or {},
        ),
    )
    return service, service.publish_policy(admin(), policy.policy_version_id)


def test_draft_is_not_indexed_and_published_is_immutable():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    draft = service.create_policy(
        admin(),
        PolicyCreate(
            scope="returns", version="v1", title="退货", content="商品签收以后七天以内可以申请退货。"
        ),
    )
    assert store.policy_chunks == {}
    service.publish_policy(admin(), draft.policy_version_id)
    assert len(store.policy_chunks[draft.policy_version_id]) == 1
    with pytest.raises(ApiError) as captured:
        service.update_policy(admin(), draft.policy_version_id, PolicyUpdate(title="篡改"))
    assert captured.value.status_code == 409


def test_rrf_gate_and_snapshot_are_auditable():
    store = InMemoryStore()
    service, policy = published_policy(store)
    result = service.debug_retrieval(
        admin(), RetrievalDebugRequest(query="退货期限是多久")
    )
    assert result["decision"] == "answer"
    assert result["reason_code"] == "ANSWERABLE"
    assert result["hits"][0]["policy_version_id"] == policy.policy_version_id
    assert result["hits"][0]["rrf_score"] > 0
    snapshot = store.get_retrieval_snapshot(result["retrieval_snapshot_id"], "tenant_demo")
    assert snapshot and snapshot.candidates


def test_acl_filter_blocks_policy_before_gate():
    store = InMemoryStore()
    service, _ = published_policy(store, acl={"region": ["上海"]})
    result = service.debug_retrieval(
        admin(), RetrievalDebugRequest(query="退货期限", filters={"region": "北京"})
    )
    assert result["decision"] == "handoff"
    assert result["reason_code"] == "NO_EVIDENCE"


def test_missing_product_context_clarifies():
    store = InMemoryStore()
    service, _ = published_policy(store)
    result = service.debug_retrieval(
        admin(), RetrievalDebugRequest(query="这个商品退货期限是多久")
    )
    assert result["decision"] == "clarify"
    assert result["reason_code"] == "MISSING_CONTEXT"


def test_old_version_is_not_retrieved_after_atomic_publish():
    store = InMemoryStore()
    service, first = published_policy(store)
    second = service.create_policy(
        admin(),
        PolicyCreate(
            scope="returns",
            version="v2",
            title="新退货政策",
            content="商品签收后十四天内可以申请退货。",
        ),
    )
    service.publish_policy(admin(), second.policy_version_id)
    hits = service.debug_retrieval(admin(), RetrievalDebugRequest(query="退货期限"))["hits"]
    assert first.status == "superseded"
    assert all(hit["policy_version_id"] != first.policy_version_id for hit in hits)


def test_evaluation_worker_reads_persisted_cases_and_results():
    store = InMemoryStore()
    service, policy = published_policy(store)
    store.import_evaluation_cases(
        [
            EvaluationCase(
                evaluation_case_id="case_1",
                tenant_id="tenant_demo",
                dataset="week4_gold",
                dataset_version="v1",
                name="退货期限",
                case_type="answerable",
                input_data={"query": "退货期限", "filters": {}},
                expected={"decision": "answer", "policy_version_ids": [policy.policy_version_id]},
            )
        ]
    )
    run = service.run_evaluation(admin(), EvaluationRunRequest())
    task = next(item for item in store.runtime_tasks.values() if item.task_type == "evaluation_run")
    worker = TaskWorker(store, worker_id="week4-test")
    worker._evaluation_run(task, LeaseGuard(task.task_id, "week4-test"))
    completed = store.get_evaluation_run(run.evaluation_run_id, "tenant_demo")
    assert completed and completed.status == "completed"
    assert completed.summary["case_count"] == 1
    assert store.list_evaluation_results(run.evaluation_run_id, "tenant_demo")[0].passed
import pytest

from app.core.errors import ApiError
