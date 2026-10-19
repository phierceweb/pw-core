"""
pw-core jobs — generic job table for batches, multi-step workflows, and
long-running operations.

Public surface::

    from pw_core.jobs import (
        # Schema
        jobs, job_steps, job_events, ALL_JOB_TABLES,

        # Registry
        register_kind, get_kind, all_kinds, clear_registry,
        JobKind, DEFAULT_STATES, DEFAULT_TRANSITIONS, TERMINAL_STATES,

        # Repo
        JobRepo,

        # Runtime
        Job, get_current_job_id, current_job_id,
    )

See ``docs/jobs.md`` for the implementation reference.
"""

from pw_core.jobs._schema import (  # noqa: F401
    ALL_JOB_TABLES,
    job_events,
    job_steps,
    jobs,
)
from pw_core.jobs.registry import (  # noqa: F401
    DEFAULT_STATES,
    DEFAULT_TRANSITIONS,
    TERMINAL_STATES,
    JobKind,
    all_kinds,
    clear_registry,
    get_kind,
    register_kind,
)
from pw_core.jobs.repo import JobRepo  # noqa: F401
from pw_core.jobs.runtime import (  # noqa: F401
    Job,
    current_job_id,
    get_current_job_id,
)

__all__ = [
    # Schema
    "ALL_JOB_TABLES",
    "job_events",
    "job_steps",
    "jobs",
    # Registry
    "DEFAULT_STATES",
    "DEFAULT_TRANSITIONS",
    "TERMINAL_STATES",
    "JobKind",
    "all_kinds",
    "clear_registry",
    "get_kind",
    "register_kind",
    # Repo
    "JobRepo",
    # Runtime
    "Job",
    "current_job_id",
    "get_current_job_id",
]
