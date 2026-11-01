"""Create, calibrate, validate and conditionally activate immutable bge-m3-v3."""

from __future__ import annotations

import argparse
import json
from uuid import uuid4

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import AuthContext
from app.domain.models import EvaluationRun, RetrievalProfile
from app.repositories.factory import get_repository
from app.schemas.api import (
    RetrievalProfileActivate,
    RetrievalProfileCalibration,
    RetrievalProfileValidation,
)
from app.services.evaluation_service import EvaluationService
from app.services.market_service import MarketAssistantService
from app.services.policy_service import PolicyService

TENANT = "tenant_demo"


def run(repository, profile_id: str, dataset: str, version: str) -> EvaluationRun:
    profile = repository.get_retrieval_profile(profile_id, TENANT)
    if profile is None:
        raise RuntimeError("Profile disappeared")
    value = EvaluationRun(
        evaluation_run_id=f"eval_v4_{uuid4().hex}", tenant_id=TENANT,
        dataset=dataset, dataset_version=version,
        config_snapshot={
            "retrieval_profile_id": profile_id,
            "embedding_model": profile.embedding_model,
            "embedding_dimension": profile.embedding_dimension,
            "retriever": profile.retriever_version,
            "gate": profile.gate_version,
            "gate_config": profile.gate_config,
            "normalization": profile.normalization_version,
            "config_hash": profile.config_hash,
            "query_analyzer_version": "v4",
            "scope_match_version": "v4",
            "threshold_grid_version": "week4-v4-grid-1",
            "policy_corpus_hash": MarketAssistantService(repository)._policy_corpus_hash(TENANT),
            "split": "all",
        },
        metric_schema_version="v3", query_analyzer_version="v4",
        scope_match_version="v4", threshold_grid_version="week4-v4-grid-1",
    )
    repository.save_evaluation_run(value)
    EvaluationService(repository, settings.answerability_threshold).run(TENANT, value.evaluation_run_id)
    completed = repository.get_evaluation_run(value.evaluation_run_id, TENANT)
    if completed is None:
        raise RuntimeError("Evaluation run disappeared")
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="bge-m3-v3")
    parser.add_argument("--calibration", default="week4_gate_calibration/v1")
    parser.add_argument("--gold", default="week4_gold/v3")
    parser.add_argument("--holdout", default="week4_hard_negative/v2")
    parser.add_argument("--parent", default="bge-m3-v2")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if args.profile in {"bge-m3-v1", "bge-m3-v2"}:
        raise RuntimeError("Historical rejected profiles are read-only")
    repository = get_repository()
    service = MarketAssistantService(repository)
    auth = AuthContext(TENANT, "week4-v4", "tenant_admin", "quality", "week4-v4")
    profile = repository.get_retrieval_profile(args.profile, TENANT)
    if profile is None:
        profile = repository.save_retrieval_profile(RetrievalProfile(
            retrieval_profile_id=args.profile, tenant_id=TENANT,
            provider="openai_compatible", embedding_model="BAAI/bge-m3", embedding_dimension=1024,
            parent_profile_id=args.parent, retriever_version="hybrid-rrf-v4",
            gate_version="v4", normalization_version="query-normalizer-v4",
            gate_config={**PolicyService.default_gate_config}, status="building",
            calibration_dataset=args.calibration,
            validation_datasets=[args.gold, args.holdout],
        ))
    if profile.status == "rejected":
        raise RuntimeError(f"{args.profile} is rejected and immutable; create a new version")
    profile = repository.reindex_retrieval_profile(args.profile, TENANT)
    if profile is None or profile.status != "ready" or profile.indexed_chunks != profile.total_chunks:
        raise RuntimeError("Profile did not reach 100% published chunk coverage")

    calibration_dataset, calibration_version = args.calibration.rsplit("/", 1)
    gold_dataset, gold_version = args.gold.rsplit("/", 1)
    holdout_dataset, holdout_version = args.holdout.rsplit("/", 1)
    calibration = run(repository, args.profile, calibration_dataset, calibration_version)
    service.calibrate_retrieval_profile(
        auth, args.profile, RetrievalProfileCalibration(evaluation_run_id=calibration.evaluation_run_id)
    )
    gold = run(repository, args.profile, gold_dataset, gold_version)
    holdout = run(repository, args.profile, holdout_dataset, holdout_version)
    activated = False
    error = None
    try:
        service.validate_retrieval_profile(
            auth, args.profile,
            RetrievalProfileValidation(gold_run_id=gold.evaluation_run_id, holdout_run_id=holdout.evaluation_run_id),
        )
        if args.activate:
            service.activate_retrieval_profile(
                auth, args.profile, RetrievalProfileActivate(evaluation_run_id=gold.evaluation_run_id)
            )
            activated = True
    except (ApiError, ValueError, RuntimeError) as exc:
        error = str(exc)
    readiness = service.retrieval_profile_activation_readiness(auth, args.profile)
    print(json.dumps({
        "profile": args.profile,
        "calibration": calibration.summary,
        "gold": gold.summary,
        "holdout": holdout.summary,
        "readiness": readiness,
        "activated": activated,
        "error": error,
    }, ensure_ascii=False, indent=2))
    if error or (args.activate and not activated):
        raise RuntimeError("Week 4 Gate-v4 quality gate failed; Hash remains active")


if __name__ == "__main__":
    main()
