"""
Pre-call budget guard.

``check_budget()`` raises :class:`CostBudgetExceeded` when a planned LLM call
would push any ``block``-action scope past its hard cap. Soft threshold
crossings log but do not halt.

Usage::

    from pw_core.budget import check_budget, project_cost, CostBudgetExceeded

    projected = project_cost(
        agent_type="drafter",
        model="claude-opus-4-7",
        estimated_prompt_tokens=1500,
        estimated_completion_tokens=1000,
    )
    try:
        check_budget(agent_type="drafter", projected_cost_usd=projected)
    except CostBudgetExceeded as e:
        # Service decides: skip, fall back to cheaper agent, or requeue
        ...
"""

from __future__ import annotations

from typing import Any

from pw_foundation.exceptions import FlowException
from pw_foundation.log import get_logger
from pw_core.budget.config import _enforcement_disabled as _enforcement_disabled
from pw_core.budget.periods import (
    compute_period_end as compute_period_end,
    compute_period_start as compute_period_start,
    current_spent as current_spent,
    current_usage as current_usage,
)
from pw_core.budget.projection import project_cost as project_cost

logger = get_logger(__name__)

_current_spent = current_spent


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CostBudgetExceeded(FlowException):
    """Raised when a planned call would push a ``block`` scope past its cap.

    A :class:`~pw_foundation.exceptions.FlowException` — an expected domain
    failure, not a bug; the web layer maps it to 429.

    Attributes:
        scope_kind: ``'global' | 'agent' | 'job_kind' | 'job_id' | 'tag'``
        scope_value: Scope identifier (e.g. agent slug); ``None`` for global
        period: ``'daily' | 'monthly'``
        limit_value: The cap that was exceeded — in the tripping dimension's
            unit (USD for ``dimension='usd'``, tokens/calls otherwise)
        spent_value: Recorded usage before the planned call (same unit)
        projected_value: Projected usage of the planned call (same unit)
        dimension: ``'usd' | 'tokens' | 'calls'`` — which limit tripped
        limit_usd: The scope's USD cap, or ``0.0`` when it sets none. Always
            USD whichever dimension tripped, so a handler can format it as
            currency (likewise ``spent_usd`` / ``projected_usd``).
    """

    def __init__(
        self,
        *,
        scope_kind: str,
        scope_value: str | None,
        period: str,
        limit_value: float,
        spent_value: float,
        projected_value: float,
        dimension: str = "usd",
        usd: tuple[float, float, float] | None = None,
    ) -> None:
        self.scope_kind = scope_kind
        self.scope_value = scope_value
        self.period = period
        self.limit_value = float(limit_value)
        self.spent_value = float(spent_value)
        self.projected_value = float(projected_value)
        self.dimension = dimension
        limit_usd, spent_usd, projected_usd = usd or (
            (limit_value, spent_value, projected_value) if dimension == "usd" else (0.0, 0.0, 0.0)
        )
        self.limit_usd = float(limit_usd)
        self.spent_usd = float(spent_usd)
        self.projected_usd = float(projected_usd)
        descriptor = f"{scope_kind}:{scope_value}" if scope_value else scope_kind

        def _fmt(v: float) -> str:
            return f"{v:.4f}" if dimension == "usd" else f"{v:.0f}"

        super().__init__(
            f"budget exceeded: {descriptor} ({period}) {dimension} "
            f"spent={_fmt(self.spent_value)} + projected={_fmt(self.projected_value)} "
            f"> limit={_fmt(self.limit_value)}"
        )


# ---------------------------------------------------------------------------
# Soft threshold crossing dedupe (in-process set)
# ---------------------------------------------------------------------------

_THRESHOLD_FIRED: set[tuple[int, str, float]] = set()


def _maybe_log_threshold(
    *,
    budget: dict,
    spent_before: float,
    spent_after: float,
) -> None:
    # Soft thresholds are USD-only: the fractions anchor to limit_usd.
    thresholds = budget.get("soft_thresholds") or []
    if not thresholds or budget.get("limit_usd") is None:
        return
    limit = float(budget["limit_usd"])
    period_start = compute_period_start(budget["period"])
    for frac in thresholds:
        frac = float(frac)
        cross = limit * frac
        if spent_before < cross <= spent_after:
            key = (int(budget["id"]), str(period_start), frac)
            if key in _THRESHOLD_FIRED:
                continue
            _THRESHOLD_FIRED.add(key)
            logger.warning(
                "budget_threshold_crossed",
                scope_kind=budget["scope_kind"],
                scope_value=budget.get("scope_value"),
                period=budget["period"],
                threshold=frac,
                spent=round(spent_after, 4),
                limit=round(limit, 4),
            )


def _clear_threshold_state() -> None:
    """Testing helper — clears the in-process fired-threshold set."""
    _THRESHOLD_FIRED.clear()


# ---------------------------------------------------------------------------
# Unarmed-guard reporting
# ---------------------------------------------------------------------------

# job_id is unbounded so it stays out of the dedupe key; tags are caller-supplied
# and equally unbounded, so the set is capped rather than allowed to grow.
_NO_SCOPE_WARN_LIMIT = 512
_NO_SCOPE_WARNED: set[tuple[str | None, str | None, tuple[str, ...]]] = set()


def _clear_no_scope_state() -> None:
    """Testing helper — clears the in-process warned-scope set."""
    _NO_SCOPE_WARNED.clear()


def _log_no_scopes(
    *,
    agent_type: str | None,
    job_kind: str | None,
    job_id: int | None,
    tags: list[str] | None,
) -> None:
    """Report that the guard matched nothing, so an inert cap is visible in logs."""
    key = (agent_type, job_kind, tuple(sorted(tags or ())))
    first = key not in _NO_SCOPE_WARNED and len(_NO_SCOPE_WARNED) < _NO_SCOPE_WARN_LIMIT
    if first:
        _NO_SCOPE_WARNED.add(key)
    (logger.warning if first else logger.debug)(
        "budget_no_scopes_matched",
        agent_type=agent_type,
        job_kind=job_kind,
        job_id=job_id,
        tags=list(tags or []),
        message=(
            "no enabled llm_budgets row matched these scopes — this call is "
            "uncapped; run sync_budgets_from_yaml() if budgets.yaml defines one"
        ),
    )


# ---------------------------------------------------------------------------
# Main guard
# ---------------------------------------------------------------------------


def check_budget(
    *,
    agent_type: str | None = None,
    projected_cost_usd: float,
    projected_tokens: int | None = None,
    projected_calls: int | None = None,
    job_id: int | None = None,
    job_kind: str | None = None,
    tags: list[str] | None = None,
    override: dict[str, Any] | None = None,
) -> None:
    """Pre-call guard. Raises :class:`CostBudgetExceeded` if a block scope is over cap.

    Each budget is enforced on every dimension it configures a limit for —
    USD against ``projected_cost_usd``, tokens against ``projected_tokens``,
    calls against ``projected_calls``. Soft thresholds are USD-only.

    When no enabled scope matches, the call is uncapped and logged as
    ``budget_no_scopes_matched`` rather than passing silently.

    Args:
        agent_type: Agent slug (optional — skips agent scope when None).
        projected_cost_usd: Estimated cost of the planned call.
        projected_tokens: Estimated prompt + completion tokens (None = 0).
        projected_calls: Number of calls being planned. ``None`` counts as 1 —
            the guard fronts exactly one call by definition; pass an explicit
            ``0`` for a pure state read that plans no call.
        job_id: Current job id (checks job_id scope when given).
        job_kind: Current job kind (checks job_kind scope when given).
        tags: Tags attached to the call (checks tag scopes).
        override: When non-empty dict, short-circuits to pass. The caller is
            expected to attach a ``budget:override`` tag + outcome row.

    Raises:
        CostBudgetExceeded: First matching block scope with a dimension whose
            spent + projected exceeds its limit (``exc.dimension`` names it).
            Warn scopes log only.
    """
    if override:
        logger.info(
            "budget_override_invoked",
            agent_type=agent_type,
            reason=override.get("reason"),
            operator=override.get("operator"),
        )
        return

    if _enforcement_disabled():
        logger.debug("budget_enforcement_disabled")
        return

    from pw_core.budget.repo import BudgetRepo

    budgets = BudgetRepo().list_for_scopes(
        agent_type=agent_type, job_kind=job_kind, job_id=job_id, tags=tags
    )
    if not budgets:
        _log_no_scopes(agent_type=agent_type, job_kind=job_kind, job_id=job_id, tags=tags)
        return

    order = {"global": 0, "agent": 1, "job_kind": 2, "job_id": 3, "tag": 4}
    budgets.sort(key=lambda b: (order.get(b["scope_kind"], 99), b["period"]))

    for budget in budgets:
        usage = current_usage(budget)
        spent = float(usage["usd"])
        _maybe_log_threshold(
            budget=budget, spent_before=spent, spent_after=spent + projected_cost_usd
        )

        dimensions = (
            ("usd", budget.get("limit_usd"), spent, float(projected_cost_usd)),
            ("tokens", budget.get("limit_tokens"), usage["tokens"], projected_tokens or 0),
            (
                "calls",
                budget.get("limit_calls"),
                usage["calls"],
                1 if projected_calls is None else projected_calls,
            ),
        )
        for dimension, raw_limit, dim_spent, dim_projected in dimensions:
            if raw_limit is None:
                continue
            limit = float(raw_limit)
            if dim_spent + dim_projected <= limit:
                continue

            action = budget.get("action", "block")
            if action == "warn":
                logger.warning(
                    "budget_warn_exceeded",
                    scope_kind=budget["scope_kind"],
                    scope_value=budget.get("scope_value"),
                    period=budget["period"],
                    dimension=dimension,
                    spent=round(float(dim_spent), 4),
                    projected=round(float(dim_projected), 4),
                    limit=round(limit, 4),
                )
                continue

            raise CostBudgetExceeded(
                scope_kind=budget["scope_kind"],
                scope_value=budget.get("scope_value"),
                period=budget["period"],
                limit_value=limit,
                spent_value=dim_spent,
                projected_value=dim_projected,
                dimension=dimension,
                usd=(float(budget.get("limit_usd") or 0.0), spent, float(projected_cost_usd)),
            )
