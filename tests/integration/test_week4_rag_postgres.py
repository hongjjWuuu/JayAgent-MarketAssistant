from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.models import EvaluationRun, PolicyVersion, RetrievalProfile
from app.services.policy_chunker import content_hash
from app.services.policy_service import PolicyService

pytestmark = pytest.mark.integration


def policy(scope: str, version: str, content: str, acl: dict | None = None) -> PolicyVersion:
    return PolicyVersion(
        policy_version_id=f"policy_{uuid4().hex}",
        tenant_id="tenant_contract",
        scope=scope,
        version=version,
        title=f"{scope}-{version}",
        content=content,
        content_hash=content_hash(content),
        acl=acl or {},
    )


def test_draft_publish_rrf_acl_snapshot_and_supersede(postgres_repository):
    first = policy("week4_returns", "v1", "# 退货期限\n商品签收后七天内可以退货。", {"region": ["上海"]})
    postgres_repository.save_policy(first)
    with postgres_repository.session_factory() as session:
        count = session.scalar(
            text(
                "SELECT count(*) FROM policy_chunk pc JOIN policy_document pd "
                "ON pd.id=pc.policy_document_id WHERE pd.policy_version_id=:id"
            ),
            {"id": first.policy_version_id},
        )
    assert count == 0
    postgres_repository.publish_policy(first.policy_version_id, first.tenant_id, "admin")
    policies = PolicyService(postgres_repository)
    blocked = policies.answerability_gate(
        first.tenant_id, "退货期限", filters={"region": "北京"}
    )
    assert blocked.decision == "handoff"
    allowed = policies.answerability_gate(
        first.tenant_id, "退货期限", filters={"region": "上海"}
    )
    assert allowed.decision == "answer"
    assert allowed.hits[0].rrf_score > 0
    assert allowed.hits[0].semantic_rank is not None
    snapshot = postgres_repository.get_retrieval_snapshot(
        allowed.retrieval_snapshot_id, first.tenant_id
    )
    assert snapshot and snapshot.candidates

    second = policy("week4_returns", "v2", "# 退货期限\n商品签收后十四天内可以退货。")
    postgres_repository.save_policy(second)
    postgres_repository.publish_policy(second.policy_version_id, second.tenant_id, "admin")
    assert postgres_repository.get_policy(first.policy_version_id, first.tenant_id).status == "superseded"
    assert all(
        item.policy_version_id != first.policy_version_id
        for item in policies.search(first.tenant_id, "退货期限")
    )
    assert postgres_repository.get_retrieval_snapshot(snapshot.retrieval_snapshot_id, first.tenant_id)


def test_concurrent_publish_leaves_exactly_one_current_version(postgres_repository):
    scope = f"publish_race_{uuid4().hex}"
    values = [
        policy(scope, f"v{index}", f"并发发布政策版本 {index}，包含有效售后规则。")
        for index in (1, 2)
    ]
    for item in values:
        postgres_repository.save_policy(item)

    def publish(item: PolicyVersion):
        try:
            return postgres_repository.publish_policy(item.policy_version_id, item.tenant_id)
        except (IntegrityError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(publish, values))
    current = postgres_repository.list_policy_versions(
        "tenant_contract", scope=scope, status="published"
    )
    assert len(current) == 1


def test_ten_thousand_chunk_retrieval_p95_under_500ms(postgres_repository):
    item = policy("benchmark", f"v-{uuid4().hex}", "# 性能基线\nbenchmark 售后政策测试内容。")
    postgres_repository.save_policy(item)
    postgres_repository.publish_policy(item.policy_version_id, item.tenant_id, "benchmark")
    zero_vector = "[" + ",".join(["0"] * 1536) + "]"
    with postgres_repository.session_factory.begin() as session:
        document_id = session.scalar(
            text("SELECT id FROM policy_document WHERE policy_version_id=:id"),
            {"id": item.policy_version_id},
        )
        session.execute(
            text(
                "INSERT INTO policy_chunk "
                "(id,tenant_id,policy_document_id,content,ordinal,title_path,metadata,"
                "content_hash,embedding_model,embedding_dimension,token_count,embedding) "
                "SELECT 'bench_' || :suffix || '_' || gs, :tenant, :document, "
                "'benchmark policy chunk ' || gs, gs, '[]'::json, '{}'::json, "
                "md5(gs::text), 'hash-embedding-v1', 1536, 20, CAST(:embedding AS vector) "
                "FROM generate_series(1,9999) gs"
            ),
            {
                "suffix": uuid4().hex,
                "tenant": item.tenant_id,
                "document": document_id,
                "embedding": zero_vector,
            },
        )
    durations = []
    for _ in range(10):
        started = time.perf_counter()
        postgres_repository.search_policy_chunks(
            item.tenant_id, "benchmark", [0.0] * 1536, 5, {}
        )
        durations.append((time.perf_counter() - started) * 1000)
    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 500, f"10,000-chunk retrieval P95 was {p95:.1f}ms"


def test_profile_reindex_resume_and_quality_gated_activation(postgres_repository):
    item = policy("profile", f"v-{uuid4().hex}", "# 检索配置\n商品签收后七天内允许退货。")
    postgres_repository.save_policy(item)
    postgres_repository.publish_policy(item.policy_version_id, item.tenant_id, "admin")
    profile_id = f"hash-test-{uuid4().hex}"
    postgres_repository.save_retrieval_profile(
        RetrievalProfile(
            retrieval_profile_id=profile_id,
            tenant_id=item.tenant_id,
            provider="hash",
            embedding_model="hash-embedding-v1",
            embedding_dimension=1536,
        )
    )
    first = postgres_repository.reindex_retrieval_profile(profile_id, item.tenant_id)
    second = postgres_repository.reindex_retrieval_profile(profile_id, item.tenant_id)
    assert first and second and second.status == "ready"
    assert second.indexed_chunks == second.total_chunks
    run = EvaluationRun(
        evaluation_run_id=f"run_{uuid4().hex}",
        tenant_id=item.tenant_id,
        status="completed",
        dataset="week4_gold",
        dataset_version="v3",
        metric_schema_version="v3",
        quality_status="passed",
        config_snapshot={"retrieval_profile_id": profile_id},
    )
    postgres_repository.save_evaluation_run(run)
    active = postgres_repository.activate_retrieval_profile(
        profile_id, item.tenant_id, run.evaluation_run_id, "admin"
    )
    assert active and active.status == "active"
