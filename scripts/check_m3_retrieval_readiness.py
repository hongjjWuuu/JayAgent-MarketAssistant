from __future__ import annotations

import json

from app.core.security import AuthContext
from app.repositories.factory import get_repository
from app.services.market_service import MarketAssistantService


def main() -> None:
    repository = get_repository()
    profile = repository.get_retrieval_profile("bge-m3-v10", "tenant_demo")
    if profile is None:
        print(json.dumps({"ready": False, "reason": "PROFILE_MISSING"}))
        return
    service = MarketAssistantService(repository)
    auth = AuthContext(
        "tenant_demo",
        "m3-retrieval-init",
        "tenant_admin",
        "m3-retrieval-readiness",
        "m3-retrieval-init",
    )
    readiness = service.retrieval_profile_activation_readiness(auth, profile.retrieval_profile_id)
    print(
        json.dumps(
            {
                "ready": profile.status == "active" and readiness["activatable"],
                "profile_status": profile.status,
                **readiness,
            }
        )
    )


if __name__ == "__main__":
    main()
