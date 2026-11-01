from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

BOOTSTRAP_MARKER = "market_schema_bootstrap"


def is_current_schema_bootstrap(connection: Connection) -> bool:
    """True only for databases created by the current-model compatibility bootstrap."""

    return inspect(connection).has_table(BOOTSTRAP_MARKER)

