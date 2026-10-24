"""
Budget periods and current usage.

UTC ``daily`` / ``monthly`` period bounds, and the snapshot + live-delta usage
lookup the guard and the admin pages share.
"""

from __future__ import annotations

import datetime as dt


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------


def compute_period_start(period: str, now: dt.datetime | None = None) -> dt.date:
    """Return the UTC period start date for ``daily`` or ``monthly``."""
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if period == "daily":
        return now.date()
    if period == "monthly":
        return now.date().replace(day=1)
    raise ValueError(f"unknown period: {period!r}")


def compute_period_end(period: str, start: dt.date) -> dt.date:
    if period == "daily":
        return start + dt.timedelta(days=1)
    if period == "monthly":
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    raise ValueError(f"unknown period: {period!r}")


# ---------------------------------------------------------------------------
# Spent lookup (snapshot + live delta)
# ---------------------------------------------------------------------------


def current_usage(budget: dict, *, conn=None) -> dict[str, float | int]:
    """Return current usage for *budget* = snapshot + live delta since snapshot.

    The one definition of "spent" — the guard, the admin pages, and anything
    else reporting budget state must agree. Returns ``{"usd": float,
    "tokens": int, "calls": int}``, where ``tokens`` sums all four token
    columns — uncached prompt, completion, cache read and cache write.
    """
    from pw_core.budget.repo import BudgetSnapshotRepo, aggregate_usage

    now = dt.datetime.now(dt.timezone.utc)
    period_start = compute_period_start(budget["period"], now)
    period_end = compute_period_end(budget["period"], period_start)

    snap = BudgetSnapshotRepo().get(budget_id=budget["id"], period_start=period_start)
    if snap is None:
        return aggregate_usage(
            budget=budget, period_start=period_start, period_end=period_end, conn=conn
        )

    # Snapshot exists — add runs from last_updated on; inclusive, because the
    # aggregate behind it stopped strictly below that same cutoff.
    from sqlalchemy import and_, select

    from pw_core.budget.repo import apply_scope_filter, usage_columns
    from pw_core.db.connection import transaction
    from pw_core.llm.tracking.schema import llm_runs

    def _delta(c):
        q = select(*usage_columns()).where(
            and_(
                llm_runs.c.created_at >= snap["last_updated"],
                llm_runs.c.created_at < period_end,
                llm_runs.c.status.notin_(["cache_hit", "budget_blocked"]),
            )
        )
        return c.execute(apply_scope_filter(q, budget)).fetchone()

    if conn is not None:
        usd, tokens, calls = _delta(conn)
    else:
        with transaction() as c:
            usd, tokens, calls = _delta(c)
    return {
        "usd": float(snap["spent_usd"]) + float(usd or 0),
        "tokens": int(snap.get("spent_tokens") or 0) + int(tokens or 0),
        "calls": int(snap.get("run_count") or 0) + int(calls or 0),
    }


def current_spent(budget: dict, *, conn=None) -> float:
    """USD-only view of :func:`current_usage`."""
    return float(current_usage(budget, conn=conn)["usd"])
