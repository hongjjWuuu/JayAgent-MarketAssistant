"""Run the reproducible cold/warm 10k-chunk database retrieval benchmark."""

from __future__ import annotations

import argparse
import json
import math
import time
from uuid import uuid4

from sqlalchemy import text

from app.domain.models import PolicyVersion
from app.repositories.factory import get_repository
from app.services.policy_chunker import content_hash


def percentile(values: list[float], target: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * target) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=10_000)
    parser.add_argument("--queries", type=int, default=50)
    args = parser.parse_args()
    if args.queries < 6:
        raise ValueError("At least six queries are required (five warmups plus steady samples)")
    repository = get_repository()
    if not hasattr(repository, "session_factory"):
        raise RuntimeError("Performance verification requires PostgreSQL")
    suffix = uuid4().hex
    policy = PolicyVersion(
        policy_version_id=f"perf_policy_{suffix}",
        tenant_id="tenant_demo",
        scope=f"quality_perf_{suffix}",
        version="v1",
        title="检索性能基线",
        content="# 性能基线\nbenchmark 售后政策测试内容。",
        content_hash=content_hash("benchmark 售后政策测试内容"),
    )
    zero = "[" + ",".join(["0"] * 1536) + "]"
    try:
        repository.save_policy(policy)
        repository.publish_policy(policy.policy_version_id, policy.tenant_id, "quality-benchmark")
        with repository.session_factory.begin() as session:
            document_id = session.scalar(
                text("SELECT id FROM policy_document WHERE policy_version_id=:id"),
                {"id": policy.policy_version_id},
            )
            session.execute(
                text(
                    "INSERT INTO policy_chunk "
                    "(id,tenant_id,policy_document_id,content,ordinal,title_path,metadata,"
                    "content_hash,embedding_model,embedding_dimension,token_count,embedding) "
                    "SELECT 'quality_bench_' || :suffix || '_' || gs, :tenant, :document, "
                    "'benchmark policy chunk ' || gs, gs, '[]'::json, '{}'::json, "
                    "md5(gs::text), 'hash-embedding-v1', 1536, 20, CAST(:embedding AS vector) "
                    "FROM generate_series(1,:count) gs"
                ),
                {
                    "suffix": suffix,
                    "tenant": policy.tenant_id,
                    "document": document_id,
                    "embedding": zero,
                    "count": args.chunks - 1,
                },
            )
            session.execute(text("ANALYZE policy_chunk"))
        durations: list[float] = []
        for _ in range(args.queries):
            started = time.perf_counter()
            repository.search_policy_chunks(
                policy.tenant_id, "benchmark 售后", [0.0] * 1536, 5, {}
            )
            durations.append((time.perf_counter() - started) * 1000)
        steady = durations[5:]
        with repository.session_factory() as session:
            plan = [
                row[0]
                for row in session.execute(
                    text(
                        "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM policy_chunk "
                        "WHERE tenant_id=:tenant AND embedding IS NOT NULL "
                        "ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT 30"
                    ),
                    {"tenant": policy.tenant_id, "embedding": zero},
                )
            ]
        report = {
            "chunks": args.chunks,
            "queries": args.queries,
            "warmups_excluded": 5,
            "cold_first_ms": round(durations[0], 2),
            "steady_p50_ms": round(percentile(steady, 0.50), 2),
            "steady_p95_ms": round(percentile(steady, 0.95), 2),
            "thresholds": {"cold_first_ms": 800, "steady_p95_ms": 450},
            "explain_analyze_buffers": plan,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["cold_first_ms"] >= 800 or report["steady_p95_ms"] >= 450:
            raise RuntimeError("Retrieval performance gate failed")
    finally:
        with repository.session_factory.begin() as session:
            session.execute(
                text("DELETE FROM policy_version WHERE id=:id"),
                {"id": policy.policy_version_id},
            )


if __name__ == "__main__":
    main()
