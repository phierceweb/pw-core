"""
Cost projection for a planned LLM call.

``project_cost()`` supplies the ``projected_cost_usd`` that
:func:`~pw_core.budget.check.check_budget` weighs against each cap.
"""

from __future__ import annotations

import datetime as dt

from pw_foundation.log import get_logger
from pw_core.budget.config import _enforcement_disabled

logger = get_logger("pw_core.budget.check")  # events stay under the public module's logger


def project_cost(
    *,
    agent_type: str,
    model: str,
    provider: str | None = None,
    estimated_prompt_tokens: int = 1500,
    estimated_completion_tokens: int = 1000,
) -> float:
    """Project the USD cost of a planned call.

    Priced from the ``llm_cost_rates`` row, else the shared
    :mod:`pw_core.pricing` table, else a 24h rolling mean of
    ``llm_runs.cost_usd`` for the (agent_type, model) pair. When nothing can
    price the call the projection is unknown rather than free: it logs
    ``budget_projection_unknown`` and returns ``0.0``, leaving the cap to
    fire on recorded spend alone. Returns ``0.0`` without touching the DB
    when ``BUDGET_ENFORCEMENT_DISABLED`` is set.
    """
    if _enforcement_disabled():
        return 0.0

    from pw_core.budget.repo import CostRateRepo

    rate = CostRateRepo().get_effective(model=model)
    if rate is not None:
        return estimated_prompt_tokens / 1000.0 * float(
            rate["input_per_1k"]
        ) + estimated_completion_tokens / 1000.0 * float(rate["output_per_1k"])

    from pw_core.pricing._resolver import price_call

    listed = price_call(
        provider or "",
        model,
        prompt_tokens=estimated_prompt_tokens,
        completion_tokens=estimated_completion_tokens,
    )
    if listed is not None:
        return listed

    mean = _recent_mean_cost(agent_type=agent_type, model=model)
    if mean is not None:
        return mean

    logger.warning(
        "budget_projection_unknown",
        agent_type=agent_type,
        model=model,
        message=(
            "no cost rate, price list entry, or recent runs — this call is "
            "invisible to the cap; add an llm_cost_rates row or call "
            "pw_core.pricing.register_rates"
        ),
    )
    return 0.0


def _recent_mean_cost(*, agent_type: str, model: str) -> float | None:
    """24h mean cost for the pair, or ``None`` when there is nothing to average."""
    from sqlalchemy import and_, func, select

    from pw_core.db.connection import transaction
    from pw_core.llm.tracking._resolvers import (
        resolve_agent_type_id,
        resolve_llm_model_id,
    )
    from pw_core.llm.tracking.schema import llm_runs

    try:
        agent_id = resolve_agent_type_id(agent_type)
        model_id = resolve_llm_model_id(model)
    except Exception:
        return None

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    with transaction() as conn:
        row = conn.execute(
            select(func.avg(llm_runs.c.cost_usd)).where(
                and_(
                    llm_runs.c.agent_type_id == agent_id,
                    llm_runs.c.model_id == model_id,
                    llm_runs.c.status == "success",
                    llm_runs.c.created_at >= since,
                )
            )
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])
