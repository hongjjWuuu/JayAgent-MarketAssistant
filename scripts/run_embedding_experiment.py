"""Run the frozen v3 Hash/BGE comparison and activate BGE only after passing gates."""

from __future__ import annotations

import argparse
import json
from uuid import uuid4

from app.core.config import settings
from app.domain.models import EvaluationRun, RetrievalProfile
from app.repositories.factory import get_repository
from app.services.evaluation_service import EvaluationService


def run_profile(repository, profile: RetrievalProfile) -> EvaluationRun:
    run = EvaluationRun(
        evaluation_run_id=f"eval_profile_{uuid4().hex}",
        tenant_id=profile.tenant_id,
        dataset="week4_gold",
        dataset_version="v3",
        config_snapshot={
            "retrieval_profile_id": profile.retrieval_profile_id,
            "embedding_model": profile.embedding_model,
            "embedding_dimension": profile.embedding_dimension,
            "retriever": profile.retriever_version,
            "gate": profile.gate_version,
        },
        metric_schema_version="v3",
    )
    repository.save_evaluation_run(run)
    EvaluationService(repository, settings.answerability_threshold).run(
        run.tenant_id, run.evaluation_run_id
    )
    completed = repository.get_evaluation_run(run.evaluation_run_id, run.tenant_id)
    if completed is None:
        raise RuntimeError("evaluation run disappeared")
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="bge-m3-v1")
    args = parser.parse_args()
    repository = get_repository()
    tenant_id = "tenant_demo"
    dataset = repository.get_evaluation_dataset(tenant_id, "week4_gold", "v3")
    if dataset is None or dataset.status != "frozen" or dataset.case_count != 100:
        raise RuntimeError("week4_gold/v3 must be frozen with 100 cases")
    profiles = repository.list_retrieval_profiles(tenant_id)
    hash_profile = next((item for item in profiles if item.provider == "hash"), None)
    if hash_profile is None:
        hash_profile = repository.save_retrieval_profile(
            RetrievalProfile(
                retrieval_profile_id="hash-v1:tenant_demo",
                tenant_id=tenant_id,
                provider="hash",
                embedding_model="hash-embedding-v1",
                embedding_dimension=1536,
            )
        )
        hash_profile = repository.reindex_retrieval_profile(
            hash_profile.retrieval_profile_id, tenant_id
        )
        if hash_profile is None:
            raise RuntimeError("Hash rollback profile could not be built")
    bge = repository.get_retrieval_profile(args.profile, tenant_id)
    if bge is None:
        bge = repository.save_retrieval_profile(
            RetrievalProfile(
                retrieval_profile_id=args.profile,
                tenant_id=tenant_id,
                provider="openai_compatible",
                embedding_model="BAAI/bge-m3",
                embedding_dimension=1024,
            )
        )
    bge = repository.reindex_retrieval_profile(bge.retrieval_profile_id, tenant_id)
    if bge is None or bge.status != "ready" or bge.indexed_chunks != bge.total_chunks:
        raise RuntimeError("BGE profile did not reach 100% index coverage")
    hash_run = run_profile(repository, hash_profile)
    bge_run = run_profile(repository, bge)
    report = {
        "hash": hash_run.summary,
        "bge": bge_run.summary,
        "delta": {
            key: float(bge_run.summary.get(key, 0)) - float(hash_run.summary.get(key, 0))
            for key in ("recall_at_1", "recall_at_5", "mrr", "p95_latency_ms")
        },
        "activated": False,
    }
    comparison_ok = report["delta"]["recall_at_1"] >= -0.02
    latency_ok = float(bge_run.summary.get("p95_latency_ms", 0)) < 3000
    if bge_run.quality_status == "passed" and comparison_ok and latency_ok:
        repository.activate_retrieval_profile(
            bge.retrieval_profile_id,
            tenant_id,
            bge_run.evaluation_run_id,
            "embedding-experiment",
        )
        report["activated"] = True
    else:
        bge.status = "rejected"
        bge.evaluation_run_id = bge_run.evaluation_run_id
        repository.save_retrieval_profile(bge)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["activated"]:
        raise RuntimeError("BGE quality gate rejected; Hash profile remains active")


if __name__ == "__main__":
    main()
