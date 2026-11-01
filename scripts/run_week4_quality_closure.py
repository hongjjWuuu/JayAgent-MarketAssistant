"""Calibrate, validate and conditionally activate the immutable BGE-M3 v2 profile."""

from __future__ import annotations

import json
from uuid import uuid4

from app.core.config import settings
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

TENANT = "tenant_demo"
PROFILE = "bge-m3-v2"


def run(repository, dataset: str, version: str, split: str) -> EvaluationRun:
    profile = repository.get_retrieval_profile(PROFILE, TENANT)
    if profile is None:
        raise RuntimeError("BGE profile disappeared")
    value = EvaluationRun(
        evaluation_run_id=f"eval_quality_{uuid4().hex}",
        tenant_id=TENANT,
        dataset=dataset,
        dataset_version=version,
        config_snapshot={
            "retrieval_profile_id": PROFILE,
            "embedding_model": profile.embedding_model,
            "embedding_dimension": profile.embedding_dimension,
            "retriever": profile.retriever_version,
            "gate": profile.gate_version,
            "gate_config": profile.gate_config,
            "normalization": profile.normalization_version,
            "split": split,
        },
        metric_schema_version="v3",
    )
    repository.save_evaluation_run(value)
    EvaluationService(repository, settings.answerability_threshold).run(TENANT, value.evaluation_run_id)
    completed = repository.get_evaluation_run(value.evaluation_run_id, TENANT)
    if completed is None:
        raise RuntimeError("Evaluation run disappeared")
    return completed


def main() -> None:
    repository = get_repository()
    service = MarketAssistantService(repository)
    auth = AuthContext(TENANT, "quality-gate", "tenant_admin", "quality", "quality-gate")
    legacy = repository.get_retrieval_profile("bge-m3-v1", TENANT)
    if legacy and legacy.status != "rejected":
        legacy.status = "rejected"
        legacy.rejected_reason = "Superseded by immutable Week 4 quality closure experiment"
        repository.save_retrieval_profile(legacy)
    profile = repository.get_retrieval_profile(PROFILE, TENANT)
    if profile is None:
        profile = repository.save_retrieval_profile(
            RetrievalProfile(
                retrieval_profile_id=PROFILE,
                tenant_id=TENANT,
                provider="openai_compatible",
                embedding_model="BAAI/bge-m3",
                embedding_dimension=1024,
                gate_version="v3",
                normalization_version="query-normalizer-v3",
                parent_profile_id="bge-m3-v1",
                gate_config={
                    "gate_version": "v3",
                    "min_rrf": 0.015,
                    "semantic_only_min_score": 0.35,
                    "min_top1_top2_margin": 0.02,
                    "require_lexical_anchor_for_unknown_domain": True,
                    "require_single_policy_fact_coverage": True,
                },
            )
        )
    if profile.status == "rejected":
        raise RuntimeError("bge-m3-v2 is rejected and immutable; create a new profile version")
    profile = repository.reindex_retrieval_profile(PROFILE, TENANT)
    if profile is None or profile.status != "ready" or profile.indexed_chunks != profile.total_chunks:
        raise RuntimeError("BGE v2 did not reach 100% published chunk coverage")
    baseline_gold = run(repository, "week4_gold", "v3", "all")
    calibration = run(repository, "week4_hard_negative", "v1", "calibration")
    service.calibrate_retrieval_profile(
        auth, PROFILE, RetrievalProfileCalibration(evaluation_run_id=calibration.evaluation_run_id)
    )
    gold = run(repository, "week4_gold", "v3", "all")
    holdout = run(repository, "week4_hard_negative", "v1", "holdout")
    report = {
        "profile": PROFILE,
        "calibration": calibration.summary,
        "baseline_gold": baseline_gold.summary,
        "gold": gold.summary,
        "holdout": holdout.summary,
        "activated": False,
    }
    try:
        service.validate_retrieval_profile(
            auth,
            PROFILE,
            RetrievalProfileValidation(
                gold_run_id=gold.evaluation_run_id,
                holdout_run_id=holdout.evaluation_run_id,
            ),
        )
        service.activate_retrieval_profile(
            auth,
            PROFILE,
            RetrievalProfileActivate(evaluation_run_id=gold.evaluation_run_id),
        )
        report["activated"] = True
    finally:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["activated"]:
        raise RuntimeError("Week 4 quality gate failed; Hash remains active")


if __name__ == "__main__":
    main()
