from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.persistence.database import get_engine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or apply the M4 retention policy for a closed pilot"
    )
    parser.add_argument("--pilot-id", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now = datetime.now(UTC)
    with get_engine().begin() as connection:
        release = connection.execute(
            text(
                "SELECT tenant_id, status, closed_at FROM pilot_release "
                "WHERE id = :pilot_id FOR UPDATE"
            ),
            {"pilot_id": args.pilot_id},
        ).mappings().first()
        if release is None:
            raise SystemExit("Pilot not found")
        if release["status"] != "closed" or release["closed_at"] is None:
            raise SystemExit("Retention cleanup is only available for a closed pilot")

        conversation_ids = connection.execute(
            text(
                "SELECT DISTINCT conversation_id FROM pilot_task_assignment "
                "WHERE pilot_id = :pilot_id AND conversation_id IS NOT NULL"
            ),
            {"pilot_id": args.pilot_id},
        ).scalars().all()
        trace_cutoff = now - timedelta(days=14)
        content_cutoff = now - timedelta(days=30)
        audit_cutoff = now - timedelta(days=90)
        closed_at = release["closed_at"]
        result = {
            "pilot_id": args.pilot_id,
            "mode": "execute" if args.execute else "preview",
            "closed_at": closed_at.isoformat(),
            "conversation_count": len(conversation_ids),
            "trace_due": closed_at <= trace_cutoff,
            "content_due": closed_at <= content_cutoff,
            "audit_due": closed_at <= audit_cutoff,
        }
        if not args.execute:
            print(json.dumps(result, ensure_ascii=False))
            return

        if closed_at <= trace_cutoff and conversation_ids:
            deleted_traces = connection.execute(
                text(
                    "DELETE FROM llm_trace WHERE tenant_id = :tenant_id "
                    "AND created_at < :cutoff AND conversation_id = ANY(:conversation_ids)"
                ),
                {
                    "tenant_id": release["tenant_id"],
                    "cutoff": trace_cutoff,
                    "conversation_ids": conversation_ids,
                },
            ).rowcount
            result["deleted_llm_traces"] = deleted_traces

        if closed_at <= content_cutoff:
            if conversation_ids:
                connection.execute(
                    text(
                        "UPDATE message SET content = '[PURGED_M4]' "
                        "WHERE conversation_id = ANY(:conversation_ids)"
                    ),
                    {"conversation_ids": conversation_ids},
                )
            connection.execute(
                text(
                    "UPDATE pilot_feedback SET comment_redacted = '[PURGED_M4]', "
                    "confusion_tags = '[]'::jsonb WHERE pilot_id = :pilot_id"
                ),
                {"pilot_id": args.pilot_id},
            )
            connection.execute(
                text(
                    "UPDATE pilot_task_assignment SET notes_redacted = '[PURGED_M4]' "
                    "WHERE pilot_id = :pilot_id"
                ),
                {"pilot_id": args.pilot_id},
            )
            participants = connection.execute(
                text("SELECT id, subject FROM pilot_participant WHERE pilot_id = :pilot_id"),
                {"pilot_id": args.pilot_id},
            ).mappings().all()
            for participant in participants:
                digest = hashlib.sha256(participant["subject"].encode()).hexdigest()[:24]
                connection.execute(
                    text(
                        "UPDATE pilot_participant SET active = false, "
                        "subject = :subject WHERE id = :participant_id"
                    ),
                    {
                        "subject": f"purged:{digest}",
                        "participant_id": participant["id"],
                    },
                )
            result["content_purged"] = True

        if closed_at <= audit_cutoff:
            result["deleted_audit_events"] = connection.execute(
                text(
                    "DELETE FROM audit_event WHERE tenant_id = :tenant_id "
                    "AND created_at < :cutoff AND resource_id = :pilot_id"
                ),
                {
                    "tenant_id": release["tenant_id"],
                    "cutoff": audit_cutoff,
                    "pilot_id": args.pilot_id,
                },
            ).rowcount
        # pilot_metric_snapshot is intentionally never removed by this task.
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
