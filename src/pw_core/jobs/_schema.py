"""
SQLAlchemy schema for pw-core jobs.

Defines three framework-owned tables that unify batches, multi-step
workflows, and long-running operations across consumer projects:

- ``jobs`` — the header row for one thing a user triggered
- ``job_steps`` — ordered checkpoint log (powers resumability)
- ``job_events`` — free-form diagnostic event stream

Shares the ``metadata`` object from ``pw_core.llm.tracking.schema`` so
``metadata.create_all()`` creates both LLM tracking tables and jobs
tables in a single pass. Column types come from ``pw_core.db.types``.

See ``docs/jobs.md`` for the implementation reference.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql

from pw_core.db.types import FK_INT, JSON_, PK_INT, TIMESTAMP_US, server_now

# Shared metadata so a single create_all() builds both tracking and jobs tables.
from pw_core.llm.tracking.schema import metadata

# ---------------------------------------------------------------------------
# Unsigned byte/int variants specific to jobs columns
# ---------------------------------------------------------------------------

_UINT_PRIORITY = SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")

_UINT_PROGRESS = Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

jobs = Table(
    "jobs",
    metadata,
    Column("id", PK_INT, primary_key=True, autoincrement=True),
    Column("kind", String(64), nullable=False),
    Column(
        "parent_job_id",
        FK_INT,
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("status", String(32), nullable=False, server_default="pending"),
    # Progress
    Column("progress_total", _UINT_PROGRESS, nullable=True),
    Column("progress_current", _UINT_PROGRESS, nullable=False, server_default="0"),
    Column("current_step", String(128), nullable=True),
    # Typed JSON payloads — not queried on the hot path
    Column("inputs", JSON_, nullable=True),
    Column("outputs", JSON_, nullable=True),
    Column("error", Text, nullable=True),
    Column("error_class", String(64), nullable=True),
    # Worker lease
    Column("priority", _UINT_PRIORITY, nullable=False, server_default="50"),
    Column("claimed_by", String(128), nullable=True),
    Column("claimed_at", TIMESTAMP_US, nullable=True),
    Column("started_at", TIMESTAMP_US, nullable=True),
    Column("finished_at", TIMESTAMP_US, nullable=True),
    Column("created_by", String(128), nullable=True),
    Column("created_at", TIMESTAMP_US, nullable=False, server_default=server_now()),
    Column("updated_at", TIMESTAMP_US, nullable=False, server_default=server_now()),
    Index("idx_jobs_kind_status", "kind", "status"),
    Index("idx_jobs_status_priority_created", "status", "priority", "created_at"),
    Index("idx_jobs_parent", "parent_job_id"),
    Index("idx_jobs_claimed", "claimed_by", "claimed_at"),
    Index("idx_jobs_created_at", "created_at"),
)
"""Job header. One row per user-triggered unit of work."""


job_steps = Table(
    "job_steps",
    metadata,
    Column("id", PK_INT, primary_key=True, autoincrement=True),
    Column(
        "job_id",
        FK_INT,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("step_index", SmallInteger, nullable=False),
    Column("name", String(128), nullable=False),
    Column("status", String(32), nullable=False, server_default="running"),
    Column("inputs", JSON_, nullable=True),
    Column("outputs", JSON_, nullable=True),
    Column("error", Text, nullable=True),
    Column("started_at", TIMESTAMP_US, nullable=False, server_default=server_now()),
    Column("finished_at", TIMESTAMP_US, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    UniqueConstraint("job_id", "step_index", name="uq_job_steps_job_index"),
    Index("idx_job_steps_job_status", "job_id", "status"),
)
"""Ordered checkpoint log per job. Powers resumable orchestrators."""


job_events = Table(
    "job_events",
    metadata,
    Column("id", PK_INT, primary_key=True, autoincrement=True),
    Column(
        "job_id",
        FK_INT,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("event_type", String(64), nullable=False),
    Column("message", Text, nullable=False),
    Column("context", JSON_, nullable=True),
    Column("created_at", TIMESTAMP_US, nullable=False, server_default=server_now()),
    Index("idx_job_events_job_created", "job_id", "created_at"),
    Index("idx_job_events_type_created", "event_type", "created_at"),
)
"""Free-form diagnostic event stream. Reconstructs job narrative from DB."""


# ---------------------------------------------------------------------------
# Table list (declaration order = dependency order)
# ---------------------------------------------------------------------------

ALL_JOB_TABLES = (
    jobs,
    job_steps,
    job_events,
)
