"""Collision-safe ID allocation against a database table.

``generate_id`` (pw-foundation) makes the candidates; ``allocate_id`` checks each
against the table before handing it back::

    from pw_core.db import transaction
    from pw_core.db.ids import allocate_id

    with transaction() as conn:
        order_id = allocate_id(conn, table="orders")
"""

from __future__ import annotations

import re
from typing import Any

from pw_foundation.exceptions import InvalidInputError, PreconditionError
from pw_foundation.utils.ids import generate_id

# SQL identifiers are interpolated (not bindable as params), so restrict them to
# a plain-identifier shape — no quotes, whitespace, or SQL metacharacters.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ATTEMPTS = 24


def _check_identifier(name: str, kind: str) -> None:
    if not _IDENT_RE.match(name):
        raise InvalidInputError(f"invalid SQL {kind}: {name!r}")


def allocate_id(
    conn: Any,
    *,
    table: str,
    column: str = "id",
    preferred: str | None = None,
    size: int | None = None,
    max_attempts: int = _MAX_ATTEMPTS,
) -> str:
    """Generate a unique ID, checking for collisions against a database table.

    Tries the preferred ID first (if provided and not taken), then generates
    nanoids in a bounded retry loop.

    Args:
        conn: A SQLAlchemy ``Connection`` (inside a transaction).
        table: Table name to check for collisions.
        column: Column name for the ID (default ``"id"``).
        preferred: Try this ID first before generating.
        size: Character length for generated IDs. Defaults to the
            ``ID_LENGTH`` environment variable (clamped to 8–36), or 12.
        max_attempts: Maximum generation attempts before raising.

    Returns:
        A unique ID string (not yet inserted — caller must insert it).

    Raises:
        PreconditionError: If a unique ID could not be allocated after
            *max_attempts* tries.
    """
    from sqlalchemy import text

    _check_identifier(table, "table")
    _check_identifier(column, "column")
    check_sql = text(f"SELECT 1 FROM {table} WHERE {column} = :id LIMIT 1")

    if preferred and str(preferred).strip():
        p = str(preferred).strip()
        row = conn.execute(check_sql, {"id": p}).fetchone()
        if not row:
            return p

    for _ in range(max_attempts):
        candidate = generate_id(size=size)
        row = conn.execute(check_sql, {"id": candidate}).fetchone()
        if not row:
            return candidate

    raise PreconditionError(
        f"Could not allocate unique ID in {table}.{column} after {max_attempts} attempts"
    )


__all__ = ["allocate_id"]
