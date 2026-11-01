"""Supersede duplicate demo policies after verifying their canonical replacements.

This is intentionally tenant_demo-only and records each change in audit_event.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.domain.models import utc_now
from app.persistence.models import AuditEventRow, PolicyVersionRow
from app.repositories.factory import get_repository
from app.repositories.postgres import PostgresRepository

MAPPINGS = {
    "returns": "week41_returns",
    "damaged_goods": "week41_damaged_goods",
    "shipping_delay": "week41_shipping_delay",
    "membership": "week41_membership",
    "compensation": "week41_compensation",
}


def main() -> None:
    repository = get_repository()
    if not isinstance(repository, PostgresRepository):
        raise TypeError("This governance operation requires PostgreSQL")
    changed = 0
    with repository.session_factory.begin() as session:
        rows = list(session.scalars(select(PolicyVersionRow).where(
            PolicyVersionRow.tenant_id == "tenant_demo",
            PolicyVersionRow.scope.in_([*MAPPINGS, *MAPPINGS.values()]),
        )))
        by_scope = {row.scope: row for row in rows if row.status == "published"}
        for legacy_scope, canonical_scope in MAPPINGS.items():
            legacy, canonical = by_scope.get(legacy_scope), by_scope.get(canonical_scope)
            if legacy is None:
                continue
            if canonical is None:
                raise RuntimeError(f"Canonical policy {canonical_scope} is not published")
            legacy.status = "superseded"
            legacy.updated_at = utc_now()
            session.add(AuditEventRow(
                id=f"audit_{uuid4().hex}", tenant_id="tenant_demo",
                actor_id="week4-policy-governance",
                action="policy.semantic_scope_supersede",
                resource_type="policy_version", resource_id=legacy.id,
                request_id="week4-policy-governance",
                data={"legacy_scope": legacy_scope, "canonical_scope": canonical_scope,
                      "canonical_policy_version_id": canonical.id},
                created_at=utc_now(),
            ))
            changed += 1
    print(f"Superseded {changed} duplicate demo policies; canonical Week 4.1 policies remain published")


if __name__ == "__main__":
    main()
