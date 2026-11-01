from __future__ import annotations

from fastapi import HTTPException

from app.core.security import AuthContext
from app.domain.models import Conversation, Handoff
from app.repositories.contracts import MarketRepository


class AuthorizationService:
    """Central resource authorization; repositories still enforce tenant scope."""

    def __init__(self, repository: MarketRepository) -> None:
        self.repository = repository

    @staticmethod
    def require(auth: AuthContext, *roles: str) -> None:
        if auth.role not in roles:
            raise HTTPException(status_code=403, detail="Role is not allowed for this operation")

    @staticmethod
    def require_customer(auth: AuthContext) -> str:
        AuthorizationService.require(auth, "customer")
        if not auth.customer_id:
            raise HTTPException(status_code=403, detail="Customer identity is not linked")
        return auth.customer_id

    @staticmethod
    def authorize_conversation(auth: AuthContext, conversation: Conversation | None) -> Conversation:
        if conversation is None or auth.role == "platform_admin":
            raise HTTPException(status_code=404, detail="Conversation not found")
        if auth.role == "customer" and conversation.customer_id != auth.customer_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if auth.role not in {"customer", "agent", "tenant_admin"}:
            raise HTTPException(status_code=403, detail="Role is not allowed for this operation")
        return conversation

    def handoff_queue_scope(self, auth: AuthContext) -> set[str] | None:
        if auth.role == "tenant_admin":
            return None
        self.require(auth, "agent")
        return self.repository.list_agent_queue_ids(auth.tenant_id, auth.subject)

    def authorize_handoff(self, auth: AuthContext, handoff: Handoff | None) -> Handoff:
        if handoff is None:
            raise HTTPException(status_code=404, detail="Handoff not found")
        allowed = self.handoff_queue_scope(auth)
        if allowed is not None and handoff.queue_id not in allowed:
            raise HTTPException(status_code=404, detail="Handoff not found")
        return handoff

    @staticmethod
    def capabilities(auth: AuthContext) -> list[str]:
        return {
            "customer": ["conversation:self", "order:self", "ticket:self", "action:self"],
            "agent": ["handoff:assigned_queue", "conversation:tenant:read", "ticket:tenant:read"],
            "tenant_admin": [
                "tenant:business:read",
                "handoff:tenant",
                "policy:manage",
                "runtime:manage",
                "evaluation:manage",
            ],
            "platform_admin": ["platform:workers:read", "platform:health:read"],
        }.get(auth.role, [])
