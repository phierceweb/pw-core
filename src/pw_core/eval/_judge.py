"""
LLM-judge comparator for free-form text outputs.

The judge is itself a tracked LLM call. It receives the task description
(user prompt from the golden run), the golden output, and the replay output,
and returns a 0.0–1.0 score with a rationale.

Judge output schema (parsed from the judge model's response)::

    {"score": 0.87, "rationale": "..."}

The judge run is recorded and linked to the replay run via
``llm_run_links(relation="critic")``.

Usage::

    from pw_core.eval._judge import run_judge

    score = run_judge(
        agent_type="drafter",
        judge_agent_type="drafter_judge",
        golden_payload={"rendered_user": "...", "parsed_output": {...}},
        replay_content="<raw LLM response from replay>",
        replay_run_id=50291,
    )
"""

from __future__ import annotations

import json
import math
from typing import Any

from pw_foundation.exceptions import ClientError
from pw_foundation.log import get_logger
from pw_core.llm.parse import parse_llm_json
from pw_core.llm.router import resolve_agent
from pw_core.llm.tracking.repo import LlmRunRepo
from pw_core.llm.tracking.subrepos import LlmRunLinkRepo

logger = get_logger(__name__)

_JUDGE_SYSTEM = (
    "You are an expert evaluator. Your task is to compare two LLM outputs "
    "for the same task and score the candidate output relative to the reference.\n\n"
    "Return ONLY valid JSON in this exact format:\n"
    '{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}\n\n'
    "Score 1.0 = candidate matches or exceeds reference quality. "
    "Score 0.0 = candidate is completely wrong or empty."
)

_JUDGE_USER_TEMPLATE = """\
## Task description (from user prompt)
{task_description}

## Reference output (golden)
{golden_output}

## Candidate output (replay)
{replay_output}

Evaluate the candidate. Return JSON only.
"""


def resolve_judge(judge_agent_type: str) -> tuple[Any, dict[str, Any]]:
    """Client and chat kwargs for the judge, which model_router.yaml must declare."""
    client, cfg, _backend = resolve_agent(judge_agent_type)
    return client, cfg


def _usable_score(parsed: Any) -> float | None:
    """The verdict's score clamped to 0.0–1.0, or None when it has no finite numeric score."""
    value = parsed.get("score") if isinstance(parsed, dict) else None
    if not isinstance(value, int | float | str) or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except ValueError:
        return None
    return max(0.0, min(1.0, score)) if math.isfinite(score) else None


def run_judge(
    *,
    agent_type: str,
    judge_agent_type: str,
    golden_payload: dict,
    replay_content: str,
    replay_run_id: int,
) -> float:
    """Run an LLM judge and return a 0.0–1.0 score.

    The judge call is tracked as a new ``llm_runs`` row linked to
    ``replay_run_id`` via ``relation="critic"``.

    Args:
        agent_type: The slug of the agent type being evaluated.
        judge_agent_type: The slug of the judge agent (must be configured in
            model_router.yaml).
        golden_payload: Dict from ``GoldenSetRepo.get_payload()`` — contains
            ``rendered_user`` and ``parsed_output``.
        replay_content: Raw string response from the replay LLM call.
        replay_run_id: ID of the replay ``llm_runs`` row (for linking).

    Returns:
        The judge's score, clamped to 0.0–1.0.

    Raises:
        ClientError: If the judge call fails, or its reply has no finite numeric
            ``score``. A reply that came back is still recorded and linked.
    """
    task_desc = (golden_payload.get("rendered_user") or "")[:2000]
    golden_out = json.dumps(golden_payload.get("parsed_output") or {}, indent=2)[:3000]
    replay_out = replay_content[:3000]

    user_msg = _JUDGE_USER_TEMPLATE.format(
        task_description=task_desc,
        golden_output=golden_out,
        replay_output=replay_out,
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    # Routed like any agent: an undeclared judge raises, never a framework-chosen model.
    client, cfg = resolve_judge(judge_agent_type)
    model = cfg.pop("model")
    # Deterministic defaults only — the judge agent's YAML sampling wins when
    # set (a reasoning judge may need max_tokens well past 512).
    cfg.setdefault("temperature", 0.0)
    cfg.setdefault("max_tokens", 512)

    context = {"agent_type": agent_type, "judge_agent_type": judge_agent_type}
    try:
        raw_content, usage_raw = client.chat(messages=messages, model=model, **cfg)
    except Exception as exc:
        raise ClientError(f"judge call failed: {exc}", context=context, cause=exc) from exc

    parsed = parse_llm_json(raw_content)
    score = _usable_score(parsed)
    no_score = "judge reply has no usable score"

    judge_run_id = LlmRunRepo().record(
        agent_type=judge_agent_type,
        model=model,
        sampling=cfg,
        rendered_prompts=(_JUDGE_SYSTEM, user_msg),
        raw_response=raw_content,
        parsed_output=parsed if isinstance(parsed, dict) else None,
        tags=["eval:judge"],
        status="success" if score is not None else "failed",
        error=None if score is not None else no_score,
        usage=usage_raw,
    )
    LlmRunLinkRepo().link(parent_id=replay_run_id, child_id=judge_run_id, relation="critic")
    if score is None:
        raise ClientError(
            f"{no_score}: {raw_content[:200]!r}",
            context={**context, "judge_run_id": judge_run_id},
        )

    logger.debug(
        "judge_scored",
        agent_type=agent_type,
        judge_agent_type=judge_agent_type,
        replay_run_id=replay_run_id,
        score=score,
    )
    return score
