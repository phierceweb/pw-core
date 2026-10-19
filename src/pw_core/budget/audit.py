"""
Audit helpers for blocked and override budget events.

``record_blocked_run()`` writes a zero-cost ``llm_runs`` row with
``status='budget_blocked'`` plus ``budget:blocked`` + ``budget:scope=...``
tags so analytics can answer "how many calls did the budget stop?".

``record_override()`` attaches a ``budget:override`` tag and an
``llm_run_outcomes`` row to an existing run.
"""

from __future__ import annotations

from pw_core.budget.check import CostBudgetExceeded
from pw_core.llm.tracking.repo import LlmRunRepo
from pw_core.llm.tracking.schema import llm_run_tags
from pw_core.llm.tracking.subrepos import LlmRunOutcomeRepo


def record_blocked_run(
    *,
    agent_type: str,
    model: str,
    exc: CostBudgetExceeded,
    job_id: int | None = None,
) -> int:
    """Insert a ``status='budget_blocked'`` run with scope tags.

    Returns the new ``llm_runs.id``.
    """
    descriptor = (
        f"{exc.scope_kind}:{exc.scope_value}:{exc.period}"
        if exc.scope_value
        else f"{exc.scope_kind}:{exc.period}"
    )
    tags = ["budget:blocked", f"budget:scope={descriptor}"]

    run_id = LlmRunRepo().record(
        agent_type=agent_type,
        model=model,
        status="budget_blocked",
        usage={
            "cost_usd": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "duration_ms": 1,
        },
        error=str(exc),
        error_class="CostBudgetExceeded",
        job_id=job_id,
        tags=tags,
    )
    return int(run_id)


def record_override(
    *,
    run_id: int,
    reason: str,
    operator: str | None = None,
) -> None:
    """Tag *run_id* as a budget override and write an outcome row.

    Repeatable: the tag is written once, and the latest call's notes replace
    the earlier ``budget_override`` outcome's.
    """
    from pw_core.db.connection import transaction
    from pw_core.db.upsert import insert_ignore

    with transaction() as conn:
        insert_ignore(
            conn,
            llm_run_tags,
            {"llm_run_id": run_id, "tag": "budget:override"},
            conflict=("llm_run_id", "tag"),
        )
    LlmRunOutcomeRepo().record(
        run_id,
        outcome_kind="budget_override",
        notes=reason if not operator else f"{reason} (operator: {operator})",
    )
