"""
EvalRunner — orchestrates replay eval runs against the golden set.

Usage::

    from pf_core.eval import EvalRunner

    runner = EvalRunner(config_path="config/eval.yaml")

    report = runner.run(
        version="golden_v2",
        agent_type="drafter",
        target={"model": "anthropic/claude-opus-4-7"},
        tag_as="experiment:opus47-v5",
    )
    print(report.summary())
    report.write_html("out/drafter_opus47_v5.html")
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, NamedTuple

from pf_core.exceptions import (
    ClientError,
    ConfigurationError,
    InvalidInputError,
    PreconditionError,
)
from pf_core.jobs import Job, JobRepo
from pf_core.llm._router_loader import config_path as router_config_path
from pf_core.llm.parse import parse_llm_json
from pf_core.llm.router import list_agents, resolve_agent
from pf_core.llm.tracking.repo import LlmRunRepo
from pf_core.llm.tracking.subrepos import LlmRunMetricRepo, LlmRunOutcomeRepo
from pf_core.log import get_logger

from pf_core.eval._compare import get_comparator, split_comparator_result
from pf_core.eval._config import AgentEvalConfig, MetricGate, load_eval_config
from pf_core.eval._golden import GoldenSetRepo
from pf_core.eval._report import EvalReport, EvalResult

logger = get_logger(__name__)


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
    logger.warning(event, golden_id=golden_id, run_id=run_id, error=str(exc)[:300])
    return EvalResult(
        golden_id=golden_id, run_id=run_id, score=0.0, passed=False, error=str(exc)[:500]
    )


def _router_declares(agent_type: str) -> bool:
    """Whether model_router.yaml declares the agent; a router file that fails to load raises."""
    if not router_config_path().exists():
        return False
    return agent_type in list_agents()


class _ReplayCall(NamedTuple):
    client: Any
    model: str
    kwargs: dict[str, Any]


def _resolve_replay_call(
    agent_type: str, agent_cfg: AgentEvalConfig, target: dict[str, Any]
) -> _ReplayCall:
    """The client, model and chat kwargs for one replay.

    Routes through the model router (``target`` keys ``backend``/``model`` override). An agent
    the router doesn't declare degrades, loudly, to the target's backend (OpenRouter when none is
    named) with the target's model.
    """
    target = dict(target)
    backend_override = target.pop("backend", None)
    model_override = target.pop("model", None)
    try:
        client, base_cfg, _backend = resolve_agent(
            agent_type, backend=backend_override, model_override=model_override
        )
    except ConfigurationError as exc:
        if _router_declares(agent_type):
            raise
        logger.warning("replay_router_unavailable", agent_type=agent_type, error=str(exc)[:200])
        if backend_override is None:
            from pf_core.clients.openrouter import get_client

            client = get_client()
        else:
            from pf_core.clients.routing import get_client_for_backend

            client = get_client_for_backend(backend_override)
        base_cfg = {"model": model_override} if model_override else {}
    merged = {**base_cfg, **agent_cfg.sampling, **target}
    model = merged.pop("model", "")
    if not model:
        raise PreconditionError(
            f"No model resolved for agent_type={agent_type!r}. "
            "Set it in model_router.yaml or pass via target={'model': '...'}."
        )
    return _ReplayCall(client, model, merged)


def _judge_slug(agent_type: str, agent_cfg: AgentEvalConfig) -> str:
    return agent_cfg.judge_agent_type or f"{agent_type}_judge"


class EvalRunner:
    """Orchestrates replay evaluations against a golden set.

    Args:
        config_path: Path to ``eval.yaml``. Defaults to ``EVAL_CONFIG`` env
            var, then ``config/eval.yaml``.

    Raises:
        ConfigurationError: If eval.yaml cannot be loaded, including a named
            path that does not exist (:func:`load_eval_config`).
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._cfg = load_eval_config(config_path)

    # ------------------------------------------------------------------
    # Public: run one eval
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        version: str,
        agent_type: str,
        target: dict[str, Any],
        tag_as: str | None = None,
    ) -> EvalReport:
        """Replay the golden set and return an EvalReport.

        Creates one ``eval_replay`` job to track progress. Each golden run is
        replayed via a direct ``client.chat()`` call; the result is recorded in
        ``llm_runs``, linked to its golden via ``llm_run_links``, and scored
        in ``llm_run_outcomes``.

        Args:
            version: Golden set version (e.g. ``"golden_v2"``).
            agent_type: Agent type slug to replay (e.g. ``"drafter"``).
            target: Dict of overrides applied to the agent config for this
                experiment. Typically ``{"model": "claude-opus-4-7"}``.
                Any key present in the router config can be overridden.
            tag_as: Optional experiment tag applied to all replay runs
                (e.g. ``"experiment:opus47-v5"``). It may not start with
                ``eval:`` or equal ``agent:<agent_type>``, in any case.

        Raises:
            InvalidInputError: If ``tag_as`` collides with a tag the runner sets.
            PreconditionError: If no golden runs exist for the requested
                version + agent_type combination, or no model resolves.
            ConfigurationError: If eval.yaml's ``agents`` lists other agents
                but not ``agent_type``, the replay client, or the ``llm_judge``
                judge's, cannot be resolved, or the comparator is not
                registered. Any error building either client propagates too;
                all of these raise before a replay runs.

        Returns:
            :class:`EvalReport` with per-run scores and aggregate statistics.
        """
        from pf_core.eval._judge import resolve_judge

        # NOT f"eval:{version}" — that exact string is the golden-membership
        # tag; putting it on replays adds them to the golden set.
        replay_tags = [f"eval:replay:{version}", f"agent:{agent_type}"]
        if tag_as:
            # Case-folded: MySQL compares tags case-insensitively.
            folded = tag_as.casefold()
            if folded.startswith("eval:") or folded == replay_tags[1].casefold():
                raise InvalidInputError(
                    f"tag_as {tag_as!r} collides with a tag the runner sets: it may not start "
                    f"with 'eval:' or equal {replay_tags[1]!r}"
                )
            replay_tags.append(tag_as)

        agent_cfg = self._cfg.for_agent(agent_type)
        golden_repo = GoldenSetRepo()
        goldens = golden_repo.list(version=version, agent_type=agent_type, limit=None)

        if not goldens:
            raise PreconditionError(
                f"No golden runs found for version={version!r}, agent_type={agent_type!r}. "
                "Use GoldenSetRepo.add()/seed_from_outcomes() to add some."
            )
        # Resolved again per replay: a registered client factory owns each acquisition.
        _resolve_replay_call(agent_type, agent_cfg, target)
        if agent_cfg.compare == "llm_judge":
            resolve_judge(_judge_slug(agent_type, agent_cfg))
        else:
            get_comparator(agent_cfg.compare)

        job_id = JobRepo().create(
            kind="eval_replay",
            inputs={
                "version": version,
                "agent_type": agent_type,
                "target": target,
                "tag_as": tag_as,
            },
            created_by="pf_core.eval",
        )

        results: list[EvalResult] = []

        def _replay_one(golden_run: dict) -> EvalResult:
            golden_id = int(golden_run["id"])
            try:
                return self._run_single_replay(
                    golden_id=golden_id,
                    agent_type=agent_type,
                    agent_cfg=agent_cfg,
                    target=target,
                    tags=replay_tags,
                    golden_repo=golden_repo,
                )
            except Exception as exc:
                logger.warning(
                    "replay_failed",
                    golden_id=golden_id,
                    error=str(exc)[:300],
                )
                return EvalResult(
                    golden_id=golden_id,
                    run_id=-1,
                    score=0.0,
                    passed=False,
                    error=str(exc)[:500],
                )

        with Job(job_id) as job:
            job.transition("running")
            job.progress(total=len(goldens))
            done = [0]

            if agent_cfg.parallelism > 1:
                with ThreadPoolExecutor(max_workers=agent_cfg.parallelism) as executor:
                    fmap = {executor.submit(_replay_one, g): g for g in goldens}
                    for future in as_completed(fmap):
                        result = future.result()
                        results.append(result)
                        done[0] += 1
                        job.progress(current=done[0])
            else:
                for golden_run in goldens:
                    result = _replay_one(golden_run)
                    results.append(result)
                    done[0] += 1
                    job.progress(current=done[0])

            report = EvalReport(
                agent_type=agent_type,
                version=version,
                target=target,
                results=results,
                cfg=agent_cfg,
                job_id=job_id,
            )
            job.outputs = {
                "n_runs": len(results),
                "n_passed": sum(1 for r in results if r.passed),
                "mean_score": report.mean_score,
                "passed": report.passed,
            }
            job.transition("succeeded")

        return report

    # ------------------------------------------------------------------
    # Public: compare two experiment tags
    # ------------------------------------------------------------------

    def compare_experiments(
        self,
        *,
        baseline: str,
        candidate: str,
        agent_type: str,
    ) -> dict:
        """Pair baseline and candidate replay runs by shared golden parent.

        Delegates to :func:`pf_core.eval._experiments.compare_experiments`,
        which documents the return shape and the failure modes.
        """
        from pf_core.eval._experiments import compare_experiments as _compare

        return _compare(baseline=baseline, candidate=candidate, agent_type=agent_type)

    # ------------------------------------------------------------------
    # Internal: replay one golden run
    # ------------------------------------------------------------------

    def _run_single_replay(
        self,
        *,
        golden_id: int,
        agent_type: str,
        agent_cfg: AgentEvalConfig,
        target: dict[str, Any],
        tags: list[str],
        golden_repo: GoldenSetRepo,
    ) -> EvalResult:
        """Execute one replay and return a scored EvalResult."""
        from pf_core.eval._judge import run_judge

        payload = golden_repo.get_payload(golden_id)
        if payload is None:
            raise PreconditionError(
                f"No payload stored for golden run {golden_id}. "
                "Ensure payloads were saved when the original run was recorded."
            )

        rendered_system = payload.get("rendered_system")
        rendered_user = payload.get("rendered_user")
        golden_parsed: dict = payload.get("parsed_output") or {}
        if not golden_parsed and payload.get("raw_response"):
            # Consumers that validate post-record can store JSON-null
            # parsed_output; without this fallback every replay scores vs {}.
            reparsed = parse_llm_json(payload["raw_response"])
            if isinstance(reparsed, dict):
                golden_parsed = reparsed

        # Structured comparators need a non-empty dict golden — fail here,
        # before the replay spends tokens ({} vs {} would score 1.0).
        if agent_cfg.compare != "llm_judge" and (
            not isinstance(golden_parsed, dict) or not golden_parsed
        ):
            raise PreconditionError(
                f"golden {golden_id} has no non-empty dict parsed_output for "
                f"structured comparison (compare={agent_cfg.compare!r}). "
                "Re-promote it with dict output or use compare: llm_judge."
            )

        messages: list[dict] = []
        if rendered_system:
            messages.append({"role": "system", "content": rendered_system})
        if rendered_user:
            messages.append({"role": "user", "content": rendered_user})

        call = _resolve_replay_call(agent_type, agent_cfg, target)
        model, merged = call.model, call.kwargs
        t0 = time.monotonic()
        try:
            raw_content, usage_raw = call.client.chat(messages=messages, model=model, **merged)
            status = "success"
            error_msg: str | None = None
        except Exception as exc:
            raw_content = ""
            usage_raw = {}
            status = "failed"
            error_msg = str(exc)[:1000]
        duration_ms = int((time.monotonic() - t0) * 1000)

        parsed_replay: dict | None = None
        if raw_content:
            result = parse_llm_json(raw_content)
            if isinstance(result, dict):
                parsed_replay = result

        replay_run_id = LlmRunRepo().record(
            agent_type=agent_type,
            model=model,
            sampling={**merged, "model": model},
            rendered_prompts=(rendered_system, rendered_user),
            raw_response=raw_content or None,
            parsed_output=parsed_replay,
            tags=tags,
            parent_run=(golden_id, "replay"),
            status=status,
            error=error_msg,
            usage={**usage_raw, "duration_ms": duration_ms},
        )

        if status == "failed":
            return EvalResult(
                golden_id=golden_id,
                run_id=replay_run_id,
                score=0.0,
                passed=False,
                error=error_msg,
            )

        # Score
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
            except ClientError as exc:
                return _scoring_failed(
                    "judge_failed", exc, golden_id=golden_id, run_id=replay_run_id
                )
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
