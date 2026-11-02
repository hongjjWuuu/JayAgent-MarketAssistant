from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from app.core.config import settings
from app.core.security import AuthContext
from app.pilot_dataset import (
    PILOT_ADMIN_IDENTITY,
    PILOT_AGENT_IDENTITIES,
    PILOT_DATASET_VERSION,
    PILOT_IDENTITIES,
    m4_dataset_hash,
)
from app.repositories.factory import get_repository
from app.schemas.api import PilotCreate, PilotParticipantInput, PilotTaskInput
from app.services.market_service import MarketAssistantService

SCENARIOS = {
    "order": "查询本人订单并核对订单事实",
    "shipment": "查询异常物流并核对物流轨迹",
    "policy": "获得带版本证据的适用政策答复",
    "ticket": "预览、确认并幂等创建售后工单",
    "handoff": "携带完整上下文完成人工接管",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and freeze the M4 pilot release")
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--api-digest", required=True)
    parser.add_argument("--web-digest", required=True)
    parser.add_argument("--simulator-digest", required=True)
    parser.add_argument("--version", default="v0.7.0-m4-rc1")
    parser.add_argument("--alembic-revision", default="20261102_0020")
    parser.add_argument("--customer-count", type=int, choices=range(3, 6), default=5)
    parser.add_argument("--agent-count", type=int, choices=range(1, 3), default=2)
    parser.add_argument("--consent-confirmed", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.consent_confirmed:
        raise SystemExit("Refusing to record participant consent without --consent-confirmed")
    repository = get_repository()
    service = MarketAssistantService(repository)
    admin_subject = PILOT_ADMIN_IDENTITY[2]
    auth = AuthContext(
        tenant_id="tenant_demo",
        subject=admin_subject,
        role="tenant_admin",
        request_id="m4-release-freeze",
        username=PILOT_ADMIN_IDENTITY[1],
    )
    now = datetime.now(UTC)
    participants = [
        PilotParticipantInput(
            participant_code=code,
            role="customer",
            subject=subject,
            consented_at=now,
        )
        for code, _, subject in PILOT_IDENTITIES[: args.customer_count]
    ]
    participants.extend(
        PilotParticipantInput(
            participant_code=code,
            role="agent",
            subject=subject,
            consented_at=now,
        )
        for code, _, subject in PILOT_AGENT_IDENTITIES[: args.agent_count]
    )
    participants.append(
        PilotParticipantInput(
            participant_code=PILOT_ADMIN_IDENTITY[0],
            role="tenant_admin",
            subject=admin_subject,
            consented_at=now,
        )
    )
    tasks = [
        PilotTaskInput(
            participant_code=code,
            scenario=scenario,
            expected_outcome=expected,
        )
        for code, _, _ in PILOT_IDENTITIES[: args.customer_count]
        for scenario, expected in SCENARIOS.items()
    ]
    data = PilotCreate(
        version=args.version,
        git_sha=args.git_sha,
        image_digests={
            "api": args.api_digest,
            "web": args.web_digest,
            "simulator": args.simulator_digest,
        },
        llm_model=settings.llm_model,
        prompt_version=settings.llm_prompt_version,
        graph_version="week5-v1",
        retrieval_profile_id=settings.retrieval_profile_id,
        policy_corpus_hash=service._policy_corpus_hash(auth.tenant_id),
        dataset_version=PILOT_DATASET_VERSION,
        dataset_hash=m4_dataset_hash(),
        connector_mode=settings.business_connector_mode,
        alembic_revision=args.alembic_revision,
        input_cost_per_million=settings.llm_input_cost_per_million,
        output_cost_per_million=settings.llm_output_cost_per_million,
        participants=participants,
        tasks=tasks,
    )
    created = service.create_pilot(auth, data)
    frozen = service.freeze_pilot(auth, created["release"].pilot_id)
    print(
        json.dumps(
            {
                "pilot_id": frozen.pilot_id,
                "status": frozen.status,
                "version": frozen.version,
                "config_hash": frozen.config_hash,
                "dataset_hash": frozen.dataset_hash,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
