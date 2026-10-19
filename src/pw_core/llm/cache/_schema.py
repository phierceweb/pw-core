"""
SQLAlchemy schema for the LLM response cache.

Defines ``llm_cache_entries`` — the exact-cache index, one row per
(input_hash, agent type) — on the shared ``pw_core.llm.tracking.schema``
metadata so ``metadata.create_all()`` creates it in the same pass.

See ``docs/llm-cache.md`` for design rationale and usage.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql

from pw_core.db.types import (
    FK_BIG,
    FK_SMALL,
    JSON_,
    LARGE_TEXT,
    PK_INT,
    TIMESTAMP_US,
    server_now,
)

# Shared metadata so a single create_all() builds everything.
from pw_core.llm.tracking.schema import metadata

_UINT = Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")


llm_cache_entries = Table(
    "llm_cache_entries",
    metadata,
    Column("id", PK_INT, primary_key=True, autoincrement=True),
    # input_hash is a SHA256 hex digest — always 64 chars
    Column("input_hash", String(64), nullable=False),
    Column(
        "agent_type_id",
        FK_SMALL,
        ForeignKey("llm_agent_types.id"),
        nullable=False,
    ),
    Column(
        "model_id",
        FK_SMALL,
        ForeignKey("llm_models.id"),
        nullable=False,
    ),
    Column(
        "source_run_id",
        FK_BIG,
        ForeignKey("llm_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Denormalized response — avoids payload join on every hit
    Column("parsed_output", JSON_, nullable=True),
    Column("raw_response", LARGE_TEXT, nullable=True),
    # Analytics + TTL — no size-based eviction reads these
    Column("hit_count", _UINT, nullable=False, server_default="0"),
    Column("last_hit_at", TIMESTAMP_US, nullable=True),
    Column("expires_at", TIMESTAMP_US, nullable=True),
    Column("created_at", TIMESTAMP_US, nullable=False, server_default=server_now()),
    UniqueConstraint("input_hash", "agent_type_id", name="uq_llm_cache_input_hash_agent"),
    Index("idx_llm_cache_agent_expires", "agent_type_id", "expires_at"),
    Index("idx_llm_cache_source_run", "source_run_id"),
)
"""Exact-cache index. One row per (input_hash, agent_type) → response mapping.

``input_hash`` covers the request but not the agent, so it is not unique alone.
"""


ALL_CACHE_TABLES = (llm_cache_entries,)
