"""
EvalRunner — orchestrates replay eval runs against the golden set.

Usage::

    from pw_core.eval import EvalRunner

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

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, NamedTuple

from pw_foundation.exceptions import (
    ConfigurationError,
    InvalidInputError,
    PreconditionError,
)
from pw_core.jobs import Job, JobRepo
from pw_core.llm._router_loader import config_path as router_config_path
from pw_core.llm.parse import parse_llm_json
from pw_core.llm.router import list_agents, resolve_agent
from pw_core.llm.tracking.repo import LlmRunRepo
from pw_core.utils._errors import error_text
from pw_foundation.log import get_logger

from pw_core.eval._compare import get_comparator, structured_diff
from pw_core.eval._config import AgentEvalConfig, load_eval_config
from pw_core.eval._golden import GoldenSetRepo
from pw_core.eval._report import EvalReport, EvalResult
from pw_core.eval._scoring import (
    _apply_metric_gates as _apply_metric_gates,
    _judge_slug as _judge_slug,
    assert_diff_fields_in_goldens,
    golden_dict,
    score_replay,
)

logger = get_logger(__name__)


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
            from pw_core.clients.openrouter import get_client

            client = get_client()
        else:
            from pw_core.clients.routing import get_client_for_backend

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


def _golden_field_names(golden_repo: GoldenSetRepo, goldens: list[dict]) -> list[set[str]]:
    """Each golden output's field names, reading one payload at a time.

    A golden whose payload is missing, unreadable or holds no dict output errors in its own
    replay; it does not count here.
    """
    names: list[set[str]] = []
    for golden in goldens:
        try:
            payload = golden_repo.get_payload(int(golden["id"]))
        except Exception:
            continue  # the replay reads it again and reports the failure
        keys = set(golden_dict(payload)) if payload else set()
        if keys:
            names.append(keys)
    return names


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
                judge's, cannot be resolved, the comparator is not registered,
                or the built-in ``structured_diff``'s ``diff_fields`` names no
                field that any golden output has. Any error building either
                client propagates too; all of these raise before a replay runs.

        Returns:
            :class:`EvalReport` with per-run scores and aggregate statistics.
        """
        from pw_core.eval._judge import resolve_judge

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
        comparator = None
        if agent_cfg.compare == "llm_judge":
            resolve_judge(_judge_slug(agent_type, agent_cfg))
        else:
            comparator = get_comparator(agent_cfg.compare)
        if comparator is structured_diff and agent_cfg.diff_fields:
            assert_diff_fields_in_goldens(
                agent_type=agent_type,
                diff_fields=agent_cfg.diff_fields,
                golden_keys=_golden_field_names(golden_repo, goldens),
            )

        job_id = JobRepo().create(
            kind="eval_replay",
            inputs={
                "version": version,
                "agent_type": agent_type,
                "target": target,
                "tag_as": tag_as,
            },
            created_by="pw_core.eval",
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
                    comparator=comparator,
                )
            except Exception as exc:
                error = error_text(exc, 500)
                logger.warning("replay_failed", golden_id=golden_id, error=error[:300])
                return EvalResult(
                    golden_id=golden_id, run_id=-1, score=0.0, passed=False, error=error
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

        Delegates to :func:`pw_core.eval._experiments.compare_experiments`,
        which documents the return shape and the failure modes.
        """
        from pw_core.eval._experiments import compare_experiments as _compare

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
        comparator: Callable[..., float] | None = None,
    ) -> EvalResult:
        """Read ``golden_id``'s payload, replay it and return a scored EvalResult.

        ``comparator`` is the one ``run`` resolved (``None`` for ``llm_judge``).
        """
        payload = golden_repo.get_payload(golden_id)
        if payload is None:
            raise PreconditionError(
                f"No payload stored for golden run {golden_id}. "
                "Ensure payloads were saved when the original run was recorded."
            )

        rendered_system = payload.get("rendered_system")
        rendered_user = payload.get("rendered_user")
        golden_parsed = golden_dict(payload)

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
        if (
            comparator is structured_diff
            and agent_cfg.diff_fields
            and not any(name in golden_parsed for name in agent_cfg.diff_fields)
        ):
            raise PreconditionError(
                f"golden {golden_id} has none of diff_fields {agent_cfg.diff_fields!r} in its "
                "output; there is nothing to compare."
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
            error_msg = error_text(exc, 1000)
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

        return score_replay(
            golden_id=golden_id,
            agent_type=agent_type,
            agent_cfg=agent_cfg,
            payload=payload,
            golden_parsed=golden_parsed,
            raw_content=raw_content,
            parsed_replay=parsed_replay,
            replay_run_id=replay_run_id,
        )
