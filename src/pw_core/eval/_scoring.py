"""
Replay scoring: the golden's output dict, the judge or comparator score, and metric gates.
"""

from __future__ import annotations

import math

from pw_foundation.exceptions import ConfigurationError
from pw_foundation.log import get_logger
from pw_core.llm.parse import parse_llm_json
from pw_core.llm.tracking.subrepos import LlmRunMetricRepo, LlmRunOutcomeRepo
from pw_core.utils._errors import error_text

from pw_core.eval._compare import get_comparator, split_comparator_result
from pw_core.eval._config import AgentEvalConfig, MetricGate
from pw_core.eval._report import EvalResult

logger = get_logger("pw_core.eval._runner")  # events stay under the runner's logger

_GOLDEN_KEYS_SHOWN = 20


def _apply_metric_gates(
    score: float,
    gates: list[MetricGate],
    metrics: dict[str, float],
    *,
    run_id: int,
) -> float:
    """Return ``score``, or 0.0 if any gate fails or has no finite metric to read.

    Fails closed on an absent metric: a gate nobody can satisfy must not read
    as a pass. NaN would otherwise pass every bound.
    """
    for gate in gates:
        val = metrics.get(gate.name)
        if val is None or not math.isfinite(val):
            logger.warning(
                "eval_metric_gate_unavailable",
                run_id=run_id,
                metric=gate.name,
                value=val,
                available=sorted(metrics),
            )
            return 0.0
        if (gate.min is not None and val < gate.min) or (gate.max is not None and val > gate.max):
            logger.warning(
                "eval_metric_gate_failed",
                run_id=run_id,
                metric=gate.name,
                value=val,
                min=gate.min,
                max=gate.max,
            )
            return 0.0
    return score


def _scoring_failed(event: str, exc: Exception, *, golden_id: int, run_id: int) -> EvalResult:
    """An errored result for a replay that was recorded but could not be scored."""
    error = error_text(exc, 500)
    logger.warning(event, golden_id=golden_id, run_id=run_id, error=error[:300])
    return EvalResult(golden_id=golden_id, run_id=run_id, score=0.0, passed=False, error=error)


def _judge_slug(agent_type: str, agent_cfg: AgentEvalConfig) -> str:
    return agent_cfg.judge_agent_type or f"{agent_type}_judge"


def golden_dict(payload: dict) -> dict:
    """``parsed_output`` if a dict; if falsy, a ``str`` ``raw_response`` reparsed; else ``{}``."""
    golden_parsed: dict = payload.get("parsed_output") or {}
    raw_response = payload.get("raw_response")
    if not golden_parsed and isinstance(raw_response, str) and raw_response:
        # Consumers that validate post-record can store JSON-null
        # parsed_output; without this fallback every replay scores vs {}.
        reparsed = parse_llm_json(raw_response)
        if isinstance(reparsed, dict):
            golden_parsed = reparsed
    return golden_parsed if isinstance(golden_parsed, dict) else {}


def assert_diff_fields_in_goldens(
    *, agent_type: str, diff_fields: list[str], golden_keys: list[set[str]]
) -> None:
    """Fail when no golden output's field names include one in ``diff_fields``.

    ``golden_keys`` holds each golden output's field names; an empty list passes.

    Raises:
        ConfigurationError: If ``golden_keys`` is non-empty and none of its sets holds a
            name in ``diff_fields``.
    """
    if golden_keys and not any(f in keys for keys in golden_keys for f in diff_fields):
        names = sorted(set().union(*golden_keys))
        shown = ", ".join(repr(k) for k in names[:_GOLDEN_KEYS_SHOWN])
        if len(names) > _GOLDEN_KEYS_SHOWN:
            shown += ", …"
        raise ConfigurationError(
            f"agent {agent_type!r}: none of diff_fields {diff_fields!r} exist in any golden "
            f"output (golden keys: [{shown}]); check eval.yaml for a typo'd or renamed field."
        )


def score_replay(
    *,
    golden_id: int,
    agent_type: str,
    agent_cfg: AgentEvalConfig,
    payload: dict,
    golden_parsed: dict,
    raw_content: str,
    parsed_replay: dict | None,
    replay_run_id: int,
) -> EvalResult:
    """Score a recorded replay against its golden; records its metrics and outcome."""
    from pw_core.eval._judge import run_judge

    scored_metrics: dict[str, float] = {}
    if agent_cfg.compare == "llm_judge":
        try:
            score = run_judge(
                agent_type=agent_type,
                judge_agent_type=_judge_slug(agent_type, agent_cfg),
                golden_payload=payload,
                replay_content=raw_content,
                replay_run_id=replay_run_id,
            )
        except Exception as exc:
            return _scoring_failed("judge_failed", exc, golden_id=golden_id, run_id=replay_run_id)
    else:
        comparator = get_comparator(agent_cfg.compare)
        context = {
            "diff_fields": agent_cfg.diff_fields,
            "tolerances": agent_cfg.tolerances,
        }
        try:
            score, scored_metrics = split_comparator_result(
                comparator(
                    golden_parsed,
                    parsed_replay or {},
                    context=context,
                )
            )
        except Exception as exc:
            return _scoring_failed(
                "comparator_failed", exc, golden_id=golden_id, run_id=replay_run_id
            )

    metric_repo = LlmRunMetricRepo()
    metric_repo.record(replay_run_id, metrics=scored_metrics)

    if agent_cfg.metrics:
        score = _apply_metric_gates(
            score,
            agent_cfg.metrics,
            metric_repo.list_for_run(replay_run_id),
            run_id=replay_run_id,
        )

    LlmRunOutcomeRepo().record(
        replay_run_id,
        outcome_kind="eval_score",
        score=score,
        notes=f"vs golden {golden_id}",
    )

    passed = score >= agent_cfg.pass_threshold
    return EvalResult(
        golden_id=golden_id,
        run_id=replay_run_id,
        score=score,
        passed=passed,
        error=None,
    )
