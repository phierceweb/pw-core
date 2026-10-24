"""Tests for pw_core.eval._runner (EvalRunner) and _report (EvalReport/EvalResult)."""

from __future__ import annotations

import logging

import pytest

from pw_core.eval._config import AgentEvalConfig, EvalConfig, clear_config_cache
from pw_core.eval._report import EvalReport, EvalResult
from pw_core.llm.tracking import clear_resolver_caches, metadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    clear_resolver_caches()
    clear_config_cache()
    yield
    clear_resolver_caches()
    clear_config_cache()


@pytest.fixture
def tracking_db(pw_engine):
    metadata.create_all(pw_engine)
    yield pw_engine
    metadata.drop_all(pw_engine)


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


def test_eval_result_fields():
    r = EvalResult(golden_id=1, run_id=10, score=0.9, passed=True)
    assert r.golden_id == 1
    assert r.run_id == 10
    assert r.score == 0.9
    assert r.passed is True
    assert r.error is None


def test_eval_result_with_error():
    r = EvalResult(golden_id=1, run_id=-1, score=0.0, passed=False, error="timeout")
    assert r.error == "timeout"


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------


def _make_report(scores: list[float], threshold: float = 0.85) -> EvalReport:
    cfg = AgentEvalConfig(pass_threshold=threshold)
    results = [
        EvalResult(
            golden_id=i + 1,
            run_id=100 + i,
            score=s,
            passed=(s >= threshold),
        )
        for i, s in enumerate(scores)
    ]
    return EvalReport(
        agent_type="drafter",
        version="golden_v1",
        target={"model": "test-model"},
        results=results,
        cfg=cfg,
    )


def test_report_mean_score():
    report = _make_report([0.8, 0.9, 1.0])
    assert report.mean_score == pytest.approx(0.9)


def test_report_mean_score_empty():
    report = _make_report([])
    assert report.mean_score == 0.0


def test_report_passed():
    report = _make_report([0.9, 0.95, 0.87], threshold=0.85)
    assert report.passed is True


def test_report_failed():
    report = _make_report([0.5, 0.6, 0.7], threshold=0.85)
    assert report.passed is False


def test_report_pass_rate():
    report = _make_report([1.0, 0.5, 0.0], threshold=0.85)
    assert report.pass_rate == pytest.approx(1 / 3)


def test_report_excludes_error_runs_from_mean():
    cfg = AgentEvalConfig(pass_threshold=0.5)
    results = [
        EvalResult(golden_id=1, run_id=10, score=1.0, passed=True),
        EvalResult(golden_id=2, run_id=-1, score=0.0, passed=False, error="crash"),
    ]
    report = EvalReport(
        agent_type="drafter",
        version="golden_v1",
        target={},
        results=results,
        cfg=cfg,
    )
    assert report.mean_score == 1.0


def test_report_summary_contains_key_info():
    report = _make_report([0.9, 0.95], threshold=0.85)
    summary = report.summary()
    assert "drafter" in summary
    assert "golden_v1" in summary
    assert "PASS" in summary


def test_report_summary_fail_mode():
    report = _make_report([0.4, 0.5], threshold=0.85)
    assert "FAIL" in report.summary()


def _report_with_errored_replays(n_errored: int) -> EvalReport:
    results = [EvalResult(golden_id=1, run_id=10, score=1.0, passed=True)] + [
        EvalResult(golden_id=i, run_id=-1, score=0.0, passed=False, error="timeout")
        for i in range(2, 2 + n_errored)
    ]
    return EvalReport(
        agent_type="drafter",
        version="golden_v1",
        target={},
        results=results,
        cfg=AgentEvalConfig(pass_threshold=0.8),
    )


def test_report_with_an_errored_replay_fails():
    """An errored replay is a golden nobody scored; the scored ones cannot vouch for it."""
    assert _report_with_errored_replays(9).passed is False


def test_report_with_no_results_fails():
    assert _make_report([], threshold=0.0).passed is False


def test_report_summary_counts_errored_replays():
    assert "9 errored" in _report_with_errored_replays(9).summary()


def test_report_write_html(tmp_path):
    report = _make_report([0.8, 0.9, 0.7])
    out_path = tmp_path / "report.html"
    report.write_html(str(out_path))
    html = out_path.read_text()
    assert "<!DOCTYPE html>" in html
    assert "drafter" in html
    assert "golden_v1" in html


# ---------------------------------------------------------------------------
# EvalRunner — integration test with mocked _run_single_replay
# ---------------------------------------------------------------------------


def test_eval_runner_run_dispatches_to_each_golden(tracking_db, monkeypatch):
    """EvalRunner.run() calls _run_single_replay once per golden member."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_core.llm.tracking import llm_agent_types, llm_models, llm_run_payloads, llm_runs

    # Seed two golden runs
    golden_ids = []
    with tracking_db.begin() as conn:
        mid = conn.execute(
            llm_models.insert().values(name="orig-model-runner-test")
        ).inserted_primary_key[0]
        aid = conn.execute(
            llm_agent_types.insert().values(slug="runner_test_agent")
        ).inserted_primary_key[0]
        for i in range(2):
            run_id = conn.execute(
                llm_runs.insert().values(agent_type_id=aid, model_id=mid, status="success")
            ).inserted_primary_key[0]
            conn.execute(
                llm_run_payloads.insert().values(
                    llm_run_id=run_id,
                    rendered_user=f"Question {i}",
                    parsed_output={"answer": i},
                )
            )
            golden_ids.append(run_id)

    repo = GoldenSetRepo()
    for gid in golden_ids:
        repo.add(gid, version="runner_test_v1")

    calls = []

    def _fake_replay(self, *, golden_id, **kwargs):
        calls.append(golden_id)
        return EvalResult(golden_id=golden_id, run_id=9000 + golden_id, score=1.0, passed=True)

    monkeypatch.setattr(EvalRunner, "_run_single_replay", _fake_replay)
    monkeypatch.setattr("pw_core.clients.openrouter.get_client", lambda *a, **k: object())

    cfg = EvalConfig({"agents": {"runner_test_agent": {"compare": "structured_diff"}}})
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg

    report = runner.run(
        version="runner_test_v1",
        agent_type="runner_test_agent",
        target={"model": "fake"},
    )

    assert sorted(calls) == sorted(golden_ids)
    assert len(report.results) == 2
    assert report.mean_score == 1.0
    assert report.passed is True


def test_replay_runs_do_not_join_the_golden_set(tracking_db, monkeypatch):
    """A replay must not carry the golden-membership tag (``eval:<version>``)
    — that tag IS membership, so copying it onto replays makes every eval
    contaminate its own golden set (replays-of-replays on the next run)."""
    from sqlalchemy import select

    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_core.llm.tracking import (
        llm_agent_types,
        llm_models,
        llm_run_payloads,
        llm_run_tags,
        llm_runs,
    )

    with tracking_db.begin() as conn:
        mid = conn.execute(llm_models.insert().values(name="tagfix-model")).inserted_primary_key[0]
        aid = conn.execute(
            llm_agent_types.insert().values(slug="tagfix_agent")
        ).inserted_primary_key[0]
        gid = conn.execute(
            llm_runs.insert().values(agent_type_id=aid, model_id=mid, status="success")
        ).inserted_primary_key[0]
        conn.execute(
            llm_run_payloads.insert().values(
                llm_run_id=gid, rendered_user="Q", parsed_output={"answer": 1}
            )
        )

    repo = GoldenSetRepo()
    repo.add(gid, version="tagfix_v1")

    class _FakeClient:
        def chat(self, *, messages, model="", **kwargs):
            return '{"answer": 1}', {"duration_ms": 1}

    monkeypatch.setattr("pw_core.clients.openrouter.get_client", lambda *a, **k: _FakeClient())

    cfg = EvalConfig({"agents": {"tagfix_agent": {"compare": "structured_diff"}}})
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg

    report = runner.run(
        version="tagfix_v1",
        agent_type="tagfix_agent",
        target={"model": "candidate-model"},
        tag_as="experiment:tagfix",
    )
    assert report.results and report.results[0].score == 1.0

    replay_id = report.results[0].run_id
    with tracking_db.connect() as conn:
        replay_tags = {
            r[0]
            for r in conn.execute(
                select(llm_run_tags.c.tag).where(llm_run_tags.c.llm_run_id == replay_id)
            )
        }
    assert "eval:tagfix_v1" not in replay_tags
    assert "eval:replay:tagfix_v1" in replay_tags
    assert "experiment:tagfix" in replay_tags
    assert len(repo.list(version="tagfix_v1")) == 1


def test_golden_with_empty_parsed_output_falls_back_to_raw_response(tracking_db, monkeypatch):
    """Consumers that validate post-record can store JSON-null ``parsed_output``
    (SQL ``IS NOT NULL`` can't see it). The runner must fall back to parsing the
    stored ``raw_response`` instead of scoring every replay against ``{}``."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_core.llm.tracking import (
        llm_agent_types,
        llm_models,
        llm_run_payloads,
        llm_runs,
    )

    golden_json = '{"category": "a", "confidence": 0.9}'
    with tracking_db.begin() as conn:
        mid = conn.execute(llm_models.insert().values(name="fallback-model")).inserted_primary_key[
            0
        ]
        aid = conn.execute(
            llm_agent_types.insert().values(slug="fallback_agent")
        ).inserted_primary_key[0]
        gid = conn.execute(
            llm_runs.insert().values(agent_type_id=aid, model_id=mid, status="success")
        ).inserted_primary_key[0]
        conn.execute(
            llm_run_payloads.insert().values(
                llm_run_id=gid,
                rendered_user="Q",
                raw_response=golden_json,
                parsed_output=None,
            )
        )

    GoldenSetRepo().add(gid, version="fallback_v1")

    class _FakeClient:
        def chat(self, *, messages, model="", **kwargs):
            return golden_json, {"duration_ms": 1}

    monkeypatch.setattr("pw_core.clients.openrouter.get_client", lambda *a, **k: _FakeClient())

    cfg = EvalConfig(
        {
            "agents": {
                "fallback_agent": {
                    "compare": "structured_diff",
                    "diff_fields": ["category", "confidence"],
                }
            }
        }
    )
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg

    report = runner.run(
        version="fallback_v1", agent_type="fallback_agent", target={"model": "candidate"}
    )
    assert report.results[0].error is None
    assert report.results[0].score == 1.0


def _seed_golden(tracking_db, *, slug: str, parsed_output, raw_response=None) -> int:
    from pw_core.llm.tracking import llm_agent_types, llm_models, llm_run_payloads, llm_runs

    with tracking_db.begin() as conn:
        mid = conn.execute(
            llm_models.insert().values(name=f"seed-model-{slug}")
        ).inserted_primary_key[0]
        aid = conn.execute(llm_agent_types.insert().values(slug=slug)).inserted_primary_key[0]
        gid = conn.execute(
            llm_runs.insert().values(agent_type_id=aid, model_id=mid, status="success")
        ).inserted_primary_key[0]
        conn.execute(
            llm_run_payloads.insert().values(
                llm_run_id=gid,
                rendered_user="Q",
                raw_response=raw_response,
                parsed_output=parsed_output,
            )
        )
    return gid


def test_replay_resolves_client_through_the_router(tracking_db, monkeypatch):
    """Replays run on the router-resolved backend — not a hardcoded OpenRouter
    client — so an agent's eval measures the transport it uses in production."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner

    gid = _seed_golden(tracking_db, slug="routed_agent", parsed_output={"answer": 1})
    GoldenSetRepo().add(gid, version="routed_v1")

    models_called = []

    class _RoutedClient:
        def chat(self, *, messages, model="", **kwargs):
            models_called.append(model)
            return '{"answer": 1}', {"duration_ms": 1}

    def _fake_resolve(slug, *, backend=None, model_override=None):
        assert slug == "routed_agent"
        return (
            _RoutedClient(),
            {"model": model_override or "routed-model", "temperature": 0.1},
            "fake_backend",
        )

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _fake_resolve)

    def _boom(*a, **k):
        raise AssertionError("hardcoded OpenRouter client must not be used")

    monkeypatch.setattr("pw_core.clients.openrouter.get_client", _boom)

    cfg = EvalConfig({"agents": {"routed_agent": {"compare": "structured_diff"}}})
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg

    # No target model: the router supplies it.
    report = runner.run(version="routed_v1", agent_type="routed_agent", target={})
    assert report.results[0].error is None
    assert report.results[0].score == 1.0
    assert models_called == ["routed-model"]


def test_a_failed_judge_errors_the_replay_and_records_no_score(tracking_db, monkeypatch, caplog):
    from sqlalchemy import select

    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_core.llm.tracking import llm_run_outcomes

    gid = _seed_golden(tracking_db, slug="judged_agent", parsed_output={"answer": 1})
    GoldenSetRepo().add(gid, version="judged_v1")

    class _Replay:
        def chat(self, *, messages, model="", **kwargs):
            return '{"answer": 1}', {"duration_ms": 1}

    class _UnreachableJudge:
        def chat(self, *, messages, model="", **kwargs):
            raise ConnectionError("judge unreachable")

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent", lambda slug, **k: (_Replay(), {"model": "m"}, "fake")
    )
    monkeypatch.setattr(
        "pw_core.eval._judge.resolve_agent",
        lambda slug, **k: (_UnreachableJudge(), {"model": "judge-m"}, "fake"),
    )
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({"agents": {"judged_agent": {"compare": "llm_judge"}}})

    with caplog.at_level(logging.WARNING, logger="pw_core.eval._runner"):
        report = runner.run(version="judged_v1", agent_type="judged_agent", target={})

    (record,) = [r for r in caplog.records if "judge_failed" in r.getMessage()]
    assert record.name == "pw_core.eval._runner"
    result = report.results[0]
    assert "judge unreachable" in (result.error or "")
    assert result.run_id > 0
    assert not report.passed
    with tracking_db.connect() as conn:
        kinds = conn.execute(
            select(llm_run_outcomes.c.outcome_kind).where(
                llm_run_outcomes.c.llm_run_id == result.run_id
            )
        ).scalars()
        assert "eval_score" not in list(kinds)


def test_a_judge_error_that_is_not_a_client_error_keeps_the_replay_run_id(tracking_db, monkeypatch):
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_foundation.exceptions import DataError

    gid = _seed_golden(tracking_db, slug="judged_agent", parsed_output={"answer": 1})
    GoldenSetRepo().add(gid, version="judged_v1")

    class _Replay:
        def chat(self, *, messages, model="", **kwargs):
            return '{"answer": 1}', {"duration_ms": 1}

    def _boom(**kwargs):
        raise DataError("judge row write failed")

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent", lambda slug, **k: (_Replay(), {"model": "m"}, "fake")
    )
    monkeypatch.setattr(
        "pw_core.eval._judge.resolve_agent",
        lambda slug, **k: (object(), {"model": "judge-m"}, "fake"),
    )
    monkeypatch.setattr("pw_core.eval._judge.run_judge", _boom)
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({"agents": {"judged_agent": {"compare": "llm_judge"}}})

    report = runner.run(version="judged_v1", agent_type="judged_agent", target={})

    (result,) = report.results
    assert result.run_id == _replay_id(gid) != gid
    assert result.error and "judge row write failed" in result.error
    assert result.score == 0.0 and not result.passed and not report.passed


def test_a_judge_error_whose_text_raises_keeps_the_replay_run_id(tracking_db, monkeypatch):
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner

    class _Unprintable(Exception):
        def __str__(self) -> str:
            raise RuntimeError("no text")

    gid = _seed_golden(tracking_db, slug="judged_agent", parsed_output={"answer": 1})
    GoldenSetRepo().add(gid, version="judged_v1")

    class _Replay:
        def chat(self, *, messages, model="", **kwargs):
            return '{"answer": 1}', {"duration_ms": 1}

    def _boom(**kwargs):
        raise _Unprintable

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent", lambda slug, **k: (_Replay(), {"model": "m"}, "fake")
    )
    monkeypatch.setattr(
        "pw_core.eval._judge.resolve_agent",
        lambda slug, **k: (object(), {"model": "judge-m"}, "fake"),
    )
    monkeypatch.setattr("pw_core.eval._judge.run_judge", _boom)
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({"agents": {"judged_agent": {"compare": "llm_judge"}}})

    report = runner.run(version="judged_v1", agent_type="judged_agent", target={})

    (result,) = report.results
    assert (result.run_id, result.error) == (_replay_id(gid), "_Unprintable")
    assert result.score == 0.0 and not result.passed


def test_run_rejects_a_tag_in_the_eval_namespace(tracking_db, monkeypatch):
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_foundation.exceptions import InvalidInputError

    gid = _seed_golden(tracking_db, slug="tagged_agent", parsed_output={"answer": 1})
    GoldenSetRepo().add(gid, version="tagged_v1")

    def _never(*args, **kwargs):
        raise AssertionError("no replay may run")

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _never)
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({})

    with pytest.raises(InvalidInputError, match="Eval:tagged_v1"):
        runner.run(
            version="tagged_v1", agent_type="tagged_agent", target={}, tag_as="Eval:tagged_v1"
        )
    assert [r["id"] for r in GoldenSetRepo().list(version="tagged_v1")] == [gid]


def test_a_non_config_router_error_stops_the_run(tracking_db, monkeypatch):
    """Only ConfigurationError degrades to the OpenRouter fallback; any other
    resolution failure stops the run before a replay, never silently swallowed."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner

    gid = _seed_golden(tracking_db, slug="broken_resolve_agent", parsed_output={"a": 1})
    GoldenSetRepo().add(gid, version="broken_v1")

    def _boom_resolve(slug, **kwargs):
        raise RuntimeError("client exploded")

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _boom_resolve)

    cfg = EvalConfig({"agents": {"broken_resolve_agent": {"compare": "structured_diff"}}})
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg

    with pytest.raises(RuntimeError, match="client exploded"):
        runner.run(version="broken_v1", agent_type="broken_resolve_agent", target={"model": "x"})


def test_replay_of_a_routed_agent_never_falls_back_to_openrouter(
    tracking_db, monkeypatch, tmp_path
):
    """The OpenRouter fallback is for agents the router doesn't know; a routed agent's
    config error must stop the run, not silently run on another transport."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner
    from pw_foundation.exceptions import ConfigurationError
    from pw_core.llm.router import clear_cache

    router = tmp_path / "model_router.yaml"
    router.write_text("agents:\n  exhausted_agent:\n    model: routed-model\n")
    monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(router))
    clear_cache()
    gid = _seed_golden(tracking_db, slug="exhausted_agent", parsed_output={"a": 1})
    GoldenSetRepo().add(gid, version="exhausted_v1")

    def _exhausted(slug, **kwargs):
        raise ConfigurationError("agent 'exhausted_agent': no available backend")

    def _boom(*a, **k):
        raise AssertionError("OpenRouter fallback must not be used for a routed agent")

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _exhausted)
    monkeypatch.setattr("pw_core.clients.openrouter.get_client", _boom)

    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({"agents": {"exhausted_agent": {"compare": "structured_diff"}}})
    try:
        with pytest.raises(ConfigurationError, match="no available backend"):
            runner.run(version="exhausted_v1", agent_type="exhausted_agent", target={})
    finally:
        clear_cache()


def test_array_golden_errors_instead_of_silent_pass(tracking_db, monkeypatch):
    """A golden whose parsed_output isn't a non-empty dict cannot be
    structured-diffed: it must error before spending the replay call —
    never collapse to {} vs {} and score 1.0."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner

    gid = _seed_golden(
        tracking_db,
        slug="array_agent",
        parsed_output=[1, 2, 3],
        raw_response="[1, 2, 3]",
    )
    GoldenSetRepo().add(gid, version="array_v1")

    class _UnspentClient:
        def chat(self, **kwargs):
            raise AssertionError("replay call must not be spent on an uncomparable golden")

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent",
        lambda *a, **k: (_UnspentClient(), {"model": "x"}, "fake"),
    )

    cfg = EvalConfig({"agents": {"array_agent": {"compare": "structured_diff"}}})
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg

    report = runner.run(version="array_v1", agent_type="array_agent", target={"model": "x"})
    result = report.results[0]
    assert result.error is not None and "parsed_output" in result.error
    assert result.score == 0.0
    assert result.passed is False


def _record_goldens(*, agent_type: str, version: str, outputs: list[dict]) -> list[int]:
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.llm.tracking import LlmRunRepo

    golden_ids = []
    for i, output in enumerate(outputs):
        run_id = LlmRunRepo().record(
            agent_type=agent_type,
            model="golden-model",
            rendered_prompts=("Be brief.", f"Question {i}"),
            parsed_output=output,
        )
        GoldenSetRepo().add(run_id, version=version)
        golden_ids.append(run_id)
    return golden_ids


def _replay_id(golden_id: int) -> int:
    """The id of the run recorded as ``golden_id``'s replay."""
    from sqlalchemy import select

    from pw_core.db import transaction
    from pw_core.llm.tracking import llm_run_links

    with transaction() as conn:
        return conn.execute(
            select(llm_run_links.c.child_run_id).where(
                llm_run_links.c.parent_run_id == golden_id,
                llm_run_links.c.relation == "replay",
            )
        ).scalar_one()


def _counting_replays(monkeypatch) -> list[str]:
    """Route every replay to a client answering ``{"answer": 1}``; returns one entry per call."""
    calls: list[str] = []

    class _Client:
        def chat(self, *, messages, model="", **kwargs):
            calls.append(model)
            return '{"answer": 1}', {"duration_ms": 1}

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent",
        lambda slug, **k: (_Client(), {"model": "fake-model"}, "fake"),
    )
    return calls


def _runner_for(agent_type: str, block: dict):
    from pw_core.eval._runner import EvalRunner

    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({"agents": {agent_type: {"parallelism": 1, **block}}})
    return runner


@pytest.mark.parametrize("compare", ["structured_diff", "custom:structured_diff"])
def test_a_typoed_diff_fields_makes_no_replay_and_no_job(tracking_db, monkeypatch, compare):
    from pw_foundation.exceptions import ConfigurationError
    from pw_core.jobs import JobRepo

    _record_goldens(
        agent_type="typo_agent", version="typo_v1", outputs=[{"answer": 1}, {"answer": 2}]
    )
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("typo_agent", {"compare": compare, "diff_fields": ["anwser"]})

    with pytest.raises(ConfigurationError) as exc:
        runner.run(version="typo_v1", agent_type="typo_agent", target={})
    assert "anwser" in str(exc.value) and calls == []
    assert JobRepo().find(kind="eval_replay") == []


def test_the_diff_fields_error_lists_at_most_twenty_golden_keys():
    from pw_foundation.exceptions import ConfigurationError
    from pw_core.eval._scoring import assert_diff_fields_in_goldens

    def _message(n_keys: int) -> str:
        golden = {f"k{i:02d}": i for i in range(n_keys)}
        with pytest.raises(ConfigurationError) as exc:
            assert_diff_fields_in_goldens(
                agent_type="a", diff_fields=["anwser"], golden_keys=[set(golden)]
            )
        return str(exc.value)

    assert "'k19']" in _message(20) and "…" not in _message(20)
    assert "'k19', …]" in _message(25) and "k20" not in _message(25)


def test_a_golden_lacking_every_diff_field_errors_before_its_call(tracking_db, monkeypatch):
    titled, *headlined = _record_goldens(
        agent_type="partial_agent",
        version="partial_v1",
        outputs=[{"title": "a"}, {"headline": "b"}, {"headline": "c"}],
    )
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("partial_agent", {"compare": "structured_diff", "diff_fields": ["title"]})

    report = runner.run(version="partial_v1", agent_type="partial_agent", target={})
    assert len(calls) == 1
    results = {r.golden_id: r for r in report.results}
    assert results[titled].error is None and results[titled].run_id == _replay_id(titled)
    for golden_id in headlined:
        error = results[golden_id].error or ""
        assert f"golden {golden_id}" in error and "['title']" in error
        assert results[golden_id].run_id == -1


def test_a_golden_holding_any_one_diff_field_replays(tracking_db, monkeypatch):
    titled, bare = _record_goldens(
        agent_type="either_agent",
        version="either_v1",
        outputs=[{"title": "a"}, {"headline": "b"}],
    )
    calls = _counting_replays(monkeypatch)
    runner = _runner_for(
        "either_agent", {"compare": "structured_diff", "diff_fields": ["title", "subtitle"]}
    )

    report = runner.run(version="either_v1", agent_type="either_agent", target={})
    assert len(calls) == 1
    results = {r.golden_id: r for r in report.results}
    assert results[titled].error is None and results[titled].run_id == _replay_id(titled)
    assert f"golden {bare}" in (results[bare].error or "")
    assert "['title', 'subtitle']" in (results[bare].error or "")
    assert results[bare].run_id == -1


class _Unprintable(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no text")


def test_a_replay_whose_client_error_cannot_be_stringified_is_recorded(tracking_db, monkeypatch):
    from sqlalchemy import select

    from pw_core.db import transaction
    from pw_core.llm.tracking import llm_runs

    (gid,) = _record_goldens(
        agent_type="unprintable_agent", version="unprintable_v1", outputs=[{"answer": 1}]
    )

    class _Client:
        def chat(self, *, messages, model="", **kwargs):
            raise _Unprintable

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent", lambda slug, **k: (_Client(), {"model": "m"}, "fake")
    )
    runner = _runner_for("unprintable_agent", {"compare": "structured_diff"})

    (result,) = runner.run(
        version="unprintable_v1", agent_type="unprintable_agent", target={}
    ).results
    assert result.run_id > 0 and result.run_id == _replay_id(gid)
    assert (result.error, result.score, result.passed) == ("_Unprintable", 0.0, False)
    with transaction() as conn:
        row = conn.execute(
            select(llm_runs.c.status, llm_runs.c.error).where(llm_runs.c.id == result.run_id)
        ).one()
    assert (row.status, row.error) == ("failed", "_Unprintable")


def test_a_replay_error_that_cannot_be_stringified_errors_only_that_replay(
    tracking_db, monkeypatch
):
    _record_goldens(agent_type="unprintable_agent", version="unprintable_v1", outputs=[{"a": 1}])
    resolutions: list[str] = []

    def _resolve(slug, **kwargs):
        resolutions.append(slug)
        if len(resolutions) > 1:
            raise _Unprintable
        return object(), {"model": "m"}, "fake"

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _resolve)
    runner = _runner_for("unprintable_agent", {"compare": "structured_diff"})

    (result,) = runner.run(
        version="unprintable_v1", agent_type="unprintable_agent", target={}
    ).results
    assert (result.run_id, result.error) == (-1, "_Unprintable")


def test_a_failed_replay_is_logged_once_with_its_error_clipped_to_500(
    tracking_db, monkeypatch, caplog
):
    _record_goldens(agent_type="long_error_agent", version="long_error_v1", outputs=[{"a": 1}])

    resolutions: list[str] = []

    def _resolve(slug, **kwargs):
        resolutions.append(slug)
        if len(resolutions) > 1:
            raise RuntimeError("x" * 2000)
        return object(), {"model": "m"}, "fake"

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _resolve)
    runner = _runner_for("long_error_agent", {"compare": "structured_diff"})

    with caplog.at_level(logging.WARNING, logger="pw_core.eval._runner"):
        (result,) = runner.run(
            version="long_error_v1", agent_type="long_error_agent", target={}
        ).results

    assert result.run_id == -1
    assert result.error is not None
    assert result.error.startswith("RuntimeError: x")
    assert len(result.error) == 500
    (record,) = [r for r in caplog.records if "replay_failed" in r.getMessage()]
    assert record.name == "pw_core.eval._runner"
    assert record.levelno == logging.WARNING


def _client_fails(monkeypatch) -> dict:
    class _Client:
        def chat(self, *, messages, model="", **kwargs):
            raise RuntimeError("x" * 2000)

    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent", lambda slug, **k: (_Client(), {"model": "m"}, "fake")
    )
    return {"compare": "structured_diff"}


def _second_resolution_fails(monkeypatch) -> dict:
    resolutions: list[str] = []

    def _resolve(slug, **kwargs):
        resolutions.append(slug)
        if len(resolutions) > 1:
            raise RuntimeError("x" * 2000)
        return object(), {"model": "m"}, "fake"

    monkeypatch.setattr("pw_core.eval._runner.resolve_agent", _resolve)
    return {"compare": "structured_diff"}


def _judge_fails(monkeypatch) -> dict:
    def _boom(**kwargs):
        raise RuntimeError("x" * 2000)

    _counting_replays(monkeypatch)
    monkeypatch.setattr(
        "pw_core.eval._judge.resolve_agent", lambda slug, **k: (object(), {"model": "j"}, "fake")
    )
    monkeypatch.setattr("pw_core.eval._judge.run_judge", _boom)
    return {"compare": "llm_judge"}


def _comparator_fails(monkeypatch) -> dict:
    from pw_core.eval import _compare

    def _boom(golden, replay, *, context):
        raise RuntimeError("x" * 2000)

    _counting_replays(monkeypatch)
    monkeypatch.setitem(_compare._registry, "clip_test_raises", _boom)
    return {"compare": "clip_test_raises"}


def _stored_error(run_id: int) -> str | None:
    from sqlalchemy import select

    from pw_core.db import transaction
    from pw_core.llm.tracking import llm_runs

    with transaction() as conn:
        return conn.execute(select(llm_runs.c.error).where(llm_runs.c.id == run_id)).scalar_one()


@pytest.mark.parametrize(
    ("fail", "result_limit", "stored_limit", "event"),
    [
        pytest.param(_client_fails, 1000, 1000, None, id="client"),
        pytest.param(_second_resolution_fails, 500, None, "replay_failed", id="replay"),
        pytest.param(_judge_fails, 500, None, "judge_failed", id="judge"),
        pytest.param(_comparator_fails, 500, None, "comparator_failed", id="comparator"),
    ],
)
def test_a_failure_is_clipped_to_its_limit_on_each_path(
    tracking_db, monkeypatch, caplog, fail, result_limit, stored_limit, event
):
    _record_goldens(agent_type="clip_agent", version="clip_v1", outputs=[{"answer": 1}])
    runner = _runner_for("clip_agent", fail(monkeypatch))

    with caplog.at_level(logging.WARNING, logger="pw_core.eval._runner"):
        (result,) = runner.run(version="clip_v1", agent_type="clip_agent", target={}).results

    full = "RuntimeError: " + "x" * 2000
    assert result.error == full[:result_limit]
    if stored_limit:
        assert _stored_error(result.run_id) == full[:stored_limit]
    failures = {"replay_failed", "judge_failed", "comparator_failed"}
    logged = [
        r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg.get("event") in failures
    ]
    assert [m["event"] for m in logged] == ([event] if event else [])
    assert all(0 < len(m["error"]) <= 300 and full.startswith(m["error"]) for m in logged)


@pytest.mark.parametrize(
    ("diff_fields", "reads_per_golden"), [(["answer"], 2), ([], 1)], ids=["diff_fields", "none"]
)
def test_only_the_replay_in_flight_holds_a_payload(
    tracking_db, monkeypatch, diff_fields, reads_per_golden
):
    import weakref
    from collections import Counter

    from pw_core.eval._golden import GoldenSetRepo

    golden_ids = _record_goldens(
        agent_type="payload_agent",
        version="payload_v1",
        outputs=[{"answer": 1}, {"answer": 2}, {"answer": 3}],
    )

    class _Payload(dict):
        pass

    reads: list[int] = []
    held: list[weakref.ref] = []
    real = GoldenSetRepo.get_payload

    def counting(self, run_id):
        reads.append(run_id)
        payload = _Payload(real(self, run_id))
        held.append(weakref.ref(payload))
        return payload

    alive_at_each_call: list[int] = []

    class _Client:
        def chat(self, *, messages, model="", **kwargs):
            alive_at_each_call.append(sum(ref() is not None for ref in held))
            return '{"answer": 1}', {"duration_ms": 1}

    monkeypatch.setattr(GoldenSetRepo, "get_payload", counting)
    monkeypatch.setattr(
        "pw_core.eval._runner.resolve_agent", lambda slug, **k: (_Client(), {"model": "m"}, "fake")
    )
    block = {"compare": "structured_diff", "diff_fields": diff_fields}
    runner = _runner_for("payload_agent", block)

    for _ in range(2):
        reads.clear()
        held.clear()
        alive_at_each_call.clear()
        report = runner.run(version="payload_v1", agent_type="payload_agent", target={})
        assert all(r.error is None for r in report.results)
        assert Counter(reads) == {gid: reads_per_golden for gid in golden_ids}
        assert alive_at_each_call == [1, 1, 1]
        assert [ref() for ref in held] == [None] * len(held)


@pytest.mark.parametrize("parallelism", [1, 4])
def test_a_diff_fields_run_over_fifty_one_mib_payloads_stays_small(
    tracking_db, monkeypatch, parallelism
):
    import tracemalloc
    from collections import Counter

    from pw_core.eval._golden import GoldenSetRepo

    if tracemalloc.is_tracing():
        pytest.skip("tracemalloc is already tracing; its peak would not be this run's")
    mib = 1024 * 1024
    golden_ids = _record_goldens(
        agent_type="big_agent", version="big_v1", outputs=[{"answer": i} for i in range(50)]
    )
    reads: list[int] = []
    real = GoldenSetRepo.get_payload

    def padded(self, run_id):
        reads.append(run_id)
        payload = real(self, run_id)
        return {**payload, "parsed_output": {**payload["parsed_output"], "body": "x" * mib}}

    monkeypatch.setattr(GoldenSetRepo, "get_payload", padded)
    _counting_replays(monkeypatch)
    block = {"compare": "structured_diff", "diff_fields": ["answer"], "parallelism": parallelism}
    runner = _runner_for("big_agent", block)

    runner.run(version="big_v1", agent_type="big_agent", target={})
    reads.clear()
    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        report = runner.run(version="big_v1", agent_type="big_agent", target={})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak - baseline < (parallelism + 2) * mib
    assert len(report.results) == 50
    assert Counter(reads) == {gid: 2 for gid in golden_ids}


def test_a_typoed_diff_fields_beside_a_payload_less_golden_still_fails_the_run(
    tracking_db, monkeypatch
):
    from pw_foundation.exceptions import ConfigurationError
    from pw_core.db import transaction
    from pw_core.jobs import JobRepo
    from pw_core.llm.tracking.schema import llm_run_payloads

    _, without_payload = _record_goldens(
        agent_type="mixed_agent", version="mixed_v1", outputs=[{"answer": 1}, {"answer": 2}]
    )
    with transaction() as conn:
        conn.execute(
            llm_run_payloads.delete().where(llm_run_payloads.c.llm_run_id == without_payload)
        )
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("mixed_agent", {"compare": "structured_diff", "diff_fields": ["anwser"]})

    with pytest.raises(ConfigurationError, match="anwser"):
        runner.run(version="mixed_v1", agent_type="mixed_agent", target={})
    assert calls == []
    assert JobRepo().find(kind="eval_replay") == []


def _record_raw_response_golden(*, agent_type: str, version: str) -> int:
    """A golden whose output is stored only as ``raw_response``, its ``parsed_output`` null."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.llm.tracking import LlmRunRepo

    run_id = LlmRunRepo().record(
        agent_type=agent_type,
        model="golden-model",
        rendered_prompts=("Be brief.", "Question"),
        raw_response='{"answer": 1}',
        parsed_output=None,
    )
    GoldenSetRepo().add(run_id, version=version)
    return run_id


def test_a_diff_field_found_only_in_a_raw_response_golden_replays(tracking_db, monkeypatch):
    gid = _record_raw_response_golden(agent_type="raw_agent", version="raw_v1")
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("raw_agent", {"compare": "structured_diff", "diff_fields": ["answer"]})

    (result,) = runner.run(version="raw_v1", agent_type="raw_agent", target={}).results
    assert len(calls) == 1
    assert (result.run_id, result.error, result.score) == (_replay_id(gid), None, 1.0)


def test_a_typoed_diff_fields_names_the_keys_of_a_raw_response_golden(tracking_db, monkeypatch):
    from pw_foundation.exceptions import ConfigurationError

    _record_raw_response_golden(agent_type="raw_agent", version="raw_v1")
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("raw_agent", {"compare": "structured_diff", "diff_fields": ["anwser"]})

    with pytest.raises(ConfigurationError) as exc:
        runner.run(version="raw_v1", agent_type="raw_agent", target={})
    assert "(golden keys: ['answer'])" in str(exc.value)
    assert calls == []


def test_diff_fields_preflight_skips_llm_judge(tracking_db, monkeypatch):
    _record_goldens(agent_type="judged_typo_agent", version="judged_typo_v1", outputs=[{"a": 1}])
    calls = _counting_replays(monkeypatch)

    class _Judge:
        def chat(self, *, messages, model="", **kwargs):
            return '{"score": 1.0, "rationale": "-"}', {}

    monkeypatch.setattr(
        "pw_core.eval._judge.resolve_agent",
        lambda slug, **k: (_Judge(), {"model": "judge-model"}, "fake"),
    )
    runner = _runner_for("judged_typo_agent", {"compare": "llm_judge", "diff_fields": ["anwser"]})

    report = runner.run(version="judged_typo_v1", agent_type="judged_typo_agent", target={})
    assert len(calls) == 1
    assert report.results[0].error is None


def test_diff_fields_preflight_ignores_goldens_without_payload(tracking_db, monkeypatch):
    from pw_core.db import transaction
    from pw_core.llm.tracking.schema import llm_run_payloads

    with_payload, without_payload = _record_goldens(
        agent_type="sidecar_agent", version="sidecar_v1", outputs=[{"answer": 1}, {"answer": 2}]
    )
    with transaction() as conn:
        conn.execute(
            llm_run_payloads.delete().where(llm_run_payloads.c.llm_run_id == without_payload)
        )
    _counting_replays(monkeypatch)
    runner = _runner_for("sidecar_agent", {"compare": "structured_diff", "diff_fields": ["answer"]})

    report = runner.run(version="sidecar_v1", agent_type="sidecar_agent", target={})
    assert {r.golden_id: r.error is None for r in report.results} == {
        with_payload: True,
        without_payload: False,
    }
    (errored,) = [r for r in report.results if r.golden_id == without_payload]
    assert "No payload stored" in errored.error


def test_an_unreadable_payload_errors_only_its_own_replay(tracking_db, monkeypatch):
    from pw_core.eval._golden import GoldenSetRepo

    golden_ids = _record_goldens(
        agent_type="unreadable_agent",
        version="unreadable_v1",
        outputs=[{"answer": i} for i in range(4)],
    )
    unreadable = golden_ids[1]
    real = GoldenSetRepo.get_payload

    def get_payload(self, run_id):
        if run_id == unreadable:
            raise ValueError("payload row is corrupt")
        return real(self, run_id)

    monkeypatch.setattr(GoldenSetRepo, "get_payload", get_payload)
    calls = _counting_replays(monkeypatch)
    runner = _runner_for(
        "unreadable_agent", {"compare": "structured_diff", "diff_fields": ["answer"]}
    )

    report = runner.run(version="unreadable_v1", agent_type="unreadable_agent", target={})
    results = {r.golden_id: r for r in report.results}
    assert sorted(results) == sorted(golden_ids)
    assert len(calls) == 3
    assert results[unreadable].error == "ValueError: payload row is corrupt"
    assert (results[unreadable].run_id, results[unreadable].passed) == (-1, False)
    assert all(r.error is None for gid, r in results.items() if gid != unreadable)


def test_a_comparator_registered_over_structured_diff_skips_the_preflight(tracking_db, monkeypatch):
    from pw_core.eval import _compare

    monkeypatch.setitem(_compare._registry, "structured_diff", _compare.structured_diff)
    _compare.register_comparator("structured_diff")(lambda golden, replay, *, context: 1.0)
    _record_goldens(agent_type="own_diff_agent", version="own_diff_v1", outputs=[{"answer": 1}])
    calls = _counting_replays(monkeypatch)
    runner = _runner_for(
        "own_diff_agent", {"compare": "structured_diff", "diff_fields": ["anwser"]}
    )

    report = runner.run(version="own_diff_v1", agent_type="own_diff_agent", target={})
    assert len(calls) == 1
    assert (report.results[0].error, report.results[0].score) == (None, 1.0)


def test_diff_fields_with_every_golden_payload_missing_errors_each_replay(tracking_db, monkeypatch):
    from pw_core.db import transaction
    from pw_core.llm.tracking.schema import llm_run_payloads

    golden_ids = _record_goldens(
        agent_type="bare_agent", version="bare_v1", outputs=[{"answer": 1}, {"answer": 2}]
    )
    with transaction() as conn:
        conn.execute(llm_run_payloads.delete())
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("bare_agent", {"compare": "structured_diff", "diff_fields": ["anwser"]})

    report = runner.run(version="bare_v1", agent_type="bare_agent", target={})
    assert calls == []
    assert sorted(r.golden_id for r in report.results) == sorted(golden_ids)
    assert all(r.error and "No payload stored" in r.error for r in report.results)


@pytest.mark.parametrize("parsed_output", [[], {}], ids=["list", "dict"])
def test_diff_fields_with_only_empty_golden_outputs_errors_each_replay(
    tracking_db, monkeypatch, parsed_output
):
    from pw_core.eval._golden import GoldenSetRepo

    gid = _seed_golden(tracking_db, slug="empty_agent", parsed_output=parsed_output)
    GoldenSetRepo().add(gid, version="empty_v1")
    calls = _counting_replays(monkeypatch)
    runner = _runner_for("empty_agent", {"compare": "structured_diff", "diff_fields": ["anwser"]})

    report = runner.run(version="empty_v1", agent_type="empty_agent", target={})
    assert calls == []
    (result,) = report.results
    assert result.error and "no non-empty dict parsed_output" in result.error


def test_golden_dict_is_empty_for_a_non_dict_output_or_a_non_str_raw_response():
    from pw_core.eval._scoring import golden_dict

    assert golden_dict({"parsed_output": [1]}) == {}
    assert golden_dict({"parsed_output": None, "raw_response": {"a": 1}}) == {}
    assert golden_dict({"parsed_output": None, "raw_response": '{"a": 1}'}) == {"a": 1}


def test_eval_runner_raises_on_empty_golden_set(pw_engine):
    """PreconditionError raised when no golden runs exist."""
    from pw_core.eval._runner import EvalRunner
    from pw_foundation.exceptions import PreconditionError

    metadata.create_all(pw_engine)
    try:
        runner = EvalRunner.__new__(EvalRunner)
        runner._cfg = EvalConfig({})

        with pytest.raises(PreconditionError, match="No golden runs"):
            runner.run(
                version="empty_version",
                agent_type="nonexistent_agent",
                target={},
            )
    finally:
        metadata.drop_all(pw_engine)


def _run_gated(tracking_db, monkeypatch, *, slug: str, comparator: str, gates: list[dict]):
    """Replay one golden through a comparator + metric gates; return the report."""
    from pw_core.eval._golden import GoldenSetRepo
    from pw_core.eval._runner import EvalRunner

    gid = _seed_golden(tracking_db, slug=slug, parsed_output={"answer": 1})
    GoldenSetRepo().add(gid, version=f"{slug}_v1")

    class _FakeClient:
        def chat(self, *, messages, model="", **kwargs):
            return '{"answer": 1}', {"duration_ms": 1}

    monkeypatch.setattr("pw_core.clients.openrouter.get_client", lambda *a, **k: _FakeClient())

    cfg = EvalConfig({"agents": {slug: {"compare": comparator, "metrics": gates}}})
    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = cfg
    return runner.run(version=f"{slug}_v1", agent_type=slug, target={"model": "candidate"})


def test_metric_gate_fails_a_replay_when_the_metric_is_out_of_range(tracking_db, monkeypatch):
    """A configured gate must actually gate: a comparator-reported metric below
    ``min`` drives the replay to 0.0 even though the comparison itself scored 1.0."""
    from sqlalchemy import select

    from pw_core.eval._compare import register_comparator
    from pw_core.llm.tracking import llm_run_metrics

    @register_comparator("gate_test_low")
    def _low(golden, replay, *, context):
        return 1.0, {"field_ratio": 0.40}

    report = _run_gated(
        tracking_db,
        monkeypatch,
        slug="gate_low_agent",
        comparator="gate_test_low",
        gates=[{"name": "field_ratio", "min": 0.70}],
    )
    result = report.results[0]
    assert result.error is None
    assert result.score == 0.0
    assert result.passed is False
    assert report.passed is False

    with tracking_db.connect() as conn:
        stored = dict(
            conn.execute(
                select(llm_run_metrics.c.metric_name, llm_run_metrics.c.metric_value).where(
                    llm_run_metrics.c.llm_run_id == result.run_id
                )
            ).fetchall()
        )
    assert stored == {"field_ratio": 0.40}


def test_metric_gate_passes_a_replay_when_the_metric_is_in_range(tracking_db, monkeypatch):
    """The same gate leaves the comparator's score alone when the metric holds."""
    from pw_core.eval._compare import register_comparator

    @register_comparator("gate_test_high")
    def _high(golden, replay, *, context):
        return 1.0, {"field_ratio": 0.95}

    report = _run_gated(
        tracking_db,
        monkeypatch,
        slug="gate_high_agent",
        comparator="gate_test_high",
        gates=[{"name": "field_ratio", "min": 0.70, "max": 1.0}],
    )
    result = report.results[0]
    assert result.error is None
    assert result.score == 1.0
    assert result.passed is True
    assert report.passed is True


def test_metric_gate_with_no_source_fails_closed(tracking_db, monkeypatch):
    """A gate on a metric nothing produces must fail the replay, not be skipped —
    a gate that always passes is worse than no gate."""
    report = _run_gated(
        tracking_db,
        monkeypatch,
        slug="gate_orphan_agent",
        comparator="structured_diff",
        gates=[{"name": "never_written", "min": 0.70}],
    )
    result = report.results[0]
    assert result.score == 0.0
    assert result.passed is False


@pytest.mark.parametrize(
    ("value", "gate"),
    [
        (float("nan"), {"min": 0.5}),
        (float("nan"), {"max": 2.0}),
        (float("inf"), {"min": 0.5}),
        (float("-inf"), {"max": 2.0}),
    ],
)
def test_a_non_finite_metric_fails_its_gate(value, gate):
    from pw_core.eval._config import MetricGate
    from pw_core.eval._runner import _apply_metric_gates

    gates = [MetricGate(name="field_ratio", **gate)]
    assert _apply_metric_gates(1.0, gates, {"field_ratio": value}, run_id=1) == 0.0


@pytest.mark.parametrize("returned", [85.0, float("inf"), (0.9, {"field_ratio": float("nan")})])
def test_a_comparator_result_out_of_contract_errors_the_replay(tracking_db, monkeypatch, returned):
    """Scored as-is, 85.0 or inf would carry the mean past any threshold."""
    from sqlalchemy import select

    from pw_core.eval._compare import register_comparator
    from pw_core.llm.tracking import llm_run_metrics, llm_run_outcomes

    register_comparator("contract_test_out_of_range")(lambda golden, replay, *, context: returned)
    report = _run_gated(
        tracking_db,
        monkeypatch,
        slug="contract_agent",
        comparator="contract_test_out_of_range",
        gates=[],
    )
    result = report.results[0]
    assert "comparator" in (result.error or "")
    assert result.run_id > 0
    assert report.passed is False

    with tracking_db.connect() as conn:
        outcomes = conn.execute(
            select(llm_run_outcomes.c.outcome_kind).where(
                llm_run_outcomes.c.llm_run_id == result.run_id
            )
        ).fetchall()
        metrics = conn.execute(
            select(llm_run_metrics.c.metric_name).where(
                llm_run_metrics.c.llm_run_id == result.run_id
            )
        ).fetchall()
    assert outcomes == [] and metrics == []


def test_a_raising_comparator_errors_the_replay_and_keeps_its_run_id(tracking_db, monkeypatch):
    from sqlalchemy import select

    from pw_core.eval._compare import register_comparator
    from pw_core.llm.tracking import llm_run_outcomes

    def _raises(golden, replay, *, context):
        raise KeyError("missing")

    register_comparator("contract_test_raises")(_raises)
    report = _run_gated(
        tracking_db, monkeypatch, slug="raising_agent", comparator="contract_test_raises", gates=[]
    )
    result = report.results[0]
    assert result.error and result.run_id > 0
    assert report.passed is False
    with tracking_db.connect() as conn:
        outcomes = conn.execute(
            select(llm_run_outcomes.c.outcome_kind).where(
                llm_run_outcomes.c.llm_run_id == result.run_id
            )
        ).fetchall()
    assert outcomes == []


# ---------------------------------------------------------------------------
# compare_experiments
# ---------------------------------------------------------------------------


def _seed_compare_agent(tracking_db, *, slug: str) -> tuple[int, int]:
    from pw_core.llm.tracking import llm_agent_types, llm_models

    with tracking_db.begin() as conn:
        mid = conn.execute(
            llm_models.insert().values(name=f"cmp-model-{slug}")
        ).inserted_primary_key[0]
        aid = conn.execute(llm_agent_types.insert().values(slug=slug)).inserted_primary_key[0]
    return aid, mid


def _seed_scored_replay(
    tracking_db, *, agent_type_id: int, model_id: int, golden_id: int, tag: str, score: float
) -> int:
    from pw_core.llm.tracking import llm_run_links, llm_run_outcomes, llm_run_tags, llm_runs

    with tracking_db.begin() as conn:
        rid = conn.execute(
            llm_runs.insert().values(
                agent_type_id=agent_type_id, model_id=model_id, status="success"
            )
        ).inserted_primary_key[0]
        conn.execute(
            llm_run_links.insert().values(
                parent_run_id=golden_id, child_run_id=rid, relation="replay"
            )
        )
        conn.execute(llm_run_tags.insert().values(llm_run_id=rid, tag=tag))
        conn.execute(
            llm_run_outcomes.insert().values(llm_run_id=rid, outcome_kind="eval_score", score=score)
        )
    return rid


def _compare_runner():
    from pw_core.eval._runner import EvalRunner

    runner = EvalRunner.__new__(EvalRunner)
    runner._cfg = EvalConfig({})
    return runner


def _seed_golden_run(tracking_db, *, agent_type_id: int, model_id: int) -> int:
    from pw_core.llm.tracking import llm_runs

    with tracking_db.begin() as conn:
        return conn.execute(
            llm_runs.insert().values(
                agent_type_id=agent_type_id, model_id=model_id, status="success"
            )
        ).inserted_primary_key[0]


def test_compare_experiments_raises_when_baseline_tag_matches_nothing(tracking_db):
    """A typo'd baseline tag must raise, never return an empty comparison."""
    from pw_foundation.exceptions import PreconditionError

    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_empty_base_agent")
    gid = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    _seed_scored_replay(
        tracking_db,
        agent_type_id=aid,
        model_id=mid,
        golden_id=gid,
        tag="experiment:cand",
        score=0.9,
    )

    with pytest.raises(PreconditionError, match="experiment:no-such-tag"):
        _compare_runner().compare_experiments(
            baseline="experiment:no-such-tag",
            candidate="experiment:cand",
            agent_type="cmp_empty_base_agent",
        )


def test_compare_experiments_raises_when_candidate_tag_matches_nothing(tracking_db):
    from pw_foundation.exceptions import PreconditionError

    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_empty_cand_agent")
    gid = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    _seed_scored_replay(
        tracking_db,
        agent_type_id=aid,
        model_id=mid,
        golden_id=gid,
        tag="experiment:base",
        score=0.8,
    )

    with pytest.raises(PreconditionError, match="experiment:also-missing"):
        _compare_runner().compare_experiments(
            baseline="experiment:base",
            candidate="experiment:also-missing",
            agent_type="cmp_empty_cand_agent",
        )


def test_compare_experiments_returns_pairs_and_counts(tracking_db):
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_happy_agent")
    g1 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    g2 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    for gid, score in ((g1, 0.8), (g2, 0.6)):
        _seed_scored_replay(
            tracking_db,
            agent_type_id=aid,
            model_id=mid,
            golden_id=gid,
            tag="experiment:base",
            score=score,
        )
    for gid, score in ((g1, 0.9), (g2, 0.5)):
        _seed_scored_replay(
            tracking_db,
            agent_type_id=aid,
            model_id=mid,
            golden_id=gid,
            tag="experiment:cand",
            score=score,
        )

    result = _compare_runner().compare_experiments(
        baseline="experiment:base", candidate="experiment:cand", agent_type="cmp_happy_agent"
    )
    assert result["baseline_runs"] == 2
    assert result["candidate_runs"] == 2
    assert [p["golden_id"] for p in result["pairs"]] == sorted([g1, g2])
    by_gid = {p["golden_id"]: p for p in result["pairs"]}
    assert by_gid[g1]["baseline_score"] == pytest.approx(0.8)
    assert by_gid[g1]["candidate_score"] == pytest.approx(0.9)
    assert by_gid[g1]["delta"] == pytest.approx(0.1)
    assert by_gid[g2]["delta"] == pytest.approx(-0.1)


def test_compare_experiments_latest_replay_wins_for_reused_tag(tracking_db):
    """Duplicate scored replays under one tag: latest wins; row count exposes it."""
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_dupe_agent")
    gid = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    for score in (0.4, 0.9):
        _seed_scored_replay(
            tracking_db,
            agent_type_id=aid,
            model_id=mid,
            golden_id=gid,
            tag="experiment:base",
            score=score,
        )
    _seed_scored_replay(
        tracking_db,
        agent_type_id=aid,
        model_id=mid,
        golden_id=gid,
        tag="experiment:cand",
        score=0.7,
    )

    result = _compare_runner().compare_experiments(
        baseline="experiment:base", candidate="experiment:cand", agent_type="cmp_dupe_agent"
    )
    assert result["baseline_runs"] == 2
    assert result["candidate_runs"] == 1
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["baseline_score"] == pytest.approx(0.9)
    assert result["pairs"][0]["delta"] == pytest.approx(-0.2)


def test_compare_experiments_reports_partial_overlap_not_raises(tracking_db):
    """Counts larger than len(pairs) expose a shrunken pairing to the caller."""
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_partial_agent")
    g1 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    g2 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    g3 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    for gid in (g1, g2):
        _seed_scored_replay(
            tracking_db,
            agent_type_id=aid,
            model_id=mid,
            golden_id=gid,
            tag="experiment:base",
            score=0.7,
        )
    for gid in (g2, g3):
        _seed_scored_replay(
            tracking_db,
            agent_type_id=aid,
            model_id=mid,
            golden_id=gid,
            tag="experiment:cand",
            score=0.8,
        )

    result = _compare_runner().compare_experiments(
        baseline="experiment:base", candidate="experiment:cand", agent_type="cmp_partial_agent"
    )
    assert result["baseline_runs"] == 2
    assert result["candidate_runs"] == 2
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["golden_id"] == g2


def test_compare_experiments_names_goldens_only_one_side_scored(tracking_db):
    """An errored replay records no score, so it drops out of the pairs; the caller
    needs to know which goldens went unpaired to refuse a partial comparison."""
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_unpaired_agent")
    g1, g2, g3 = (_seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid) for _ in range(3))
    for tag, goldens in (("experiment:base", (g1, g2)), ("experiment:cand", (g2, g3))):
        for gid in goldens:
            _seed_scored_replay(
                tracking_db, agent_type_id=aid, model_id=mid, golden_id=gid, tag=tag, score=0.7
            )

    result = _compare_runner().compare_experiments(
        baseline="experiment:base", candidate="experiment:cand", agent_type="cmp_unpaired_agent"
    )
    assert result["baseline_only"] == [g1]
    assert result["candidate_only"] == [g3]


def _seed_unscored_replay(
    tracking_db, *, agent_type_id: int, model_id: int, golden_id: int, tag: str
) -> int:
    """A replay recorded with no eval_score outcome, as a failed client call leaves it."""
    from pw_core.llm.tracking import llm_run_links, llm_run_tags, llm_runs

    with tracking_db.begin() as conn:
        rid = conn.execute(
            llm_runs.insert().values(
                agent_type_id=agent_type_id, model_id=model_id, status="failed"
            )
        ).inserted_primary_key[0]
        conn.execute(
            llm_run_links.insert().values(
                parent_run_id=golden_id, child_run_id=rid, relation="replay"
            )
        )
        conn.execute(llm_run_tags.insert().values(llm_run_id=rid, tag=tag))
    return rid


def test_compare_experiments_a_later_errored_replay_unscores_the_golden(tracking_db):
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_rerun_agent")
    gid = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    seed = {"agent_type_id": aid, "model_id": mid, "golden_id": gid}
    _seed_scored_replay(tracking_db, **seed, tag="experiment:base", score=0.8)
    _seed_scored_replay(tracking_db, **seed, tag="experiment:cand", score=0.9)
    _seed_unscored_replay(tracking_db, **seed, tag="experiment:cand")

    result = _compare_runner().compare_experiments(
        baseline="experiment:base", candidate="experiment:cand", agent_type="cmp_rerun_agent"
    )
    assert result["pairs"] == []
    assert result["baseline_only"] == [gid]


def test_compare_experiments_a_later_scored_replay_restores_the_golden(tracking_db):
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_restore_agent")
    gid = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    seed = {"agent_type_id": aid, "model_id": mid, "golden_id": gid}
    _seed_scored_replay(tracking_db, **seed, tag="experiment:base", score=0.8)
    _seed_unscored_replay(tracking_db, **seed, tag="experiment:cand")
    _seed_scored_replay(tracking_db, **seed, tag="experiment:cand", score=0.9)

    result = _compare_runner().compare_experiments(
        baseline="experiment:base", candidate="experiment:cand", agent_type="cmp_restore_agent"
    )
    assert [p["golden_id"] for p in result["pairs"]] == [gid]
    assert result["baseline_only"] == []


def test_compare_experiments_reports_a_candidate_that_scored_nothing(tracking_db):
    """A candidate whose every replay errored is a failed comparison, not an
    unrunnable one: no pairs, and every baseline golden in ``baseline_only``."""
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_all_errored_agent")
    g1, g2 = (_seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid) for _ in range(2))
    for gid in (g1, g2):
        _seed_scored_replay(
            tracking_db, agent_type_id=aid, model_id=mid, golden_id=gid, tag="base", score=0.9
        )
        _seed_unscored_replay(
            tracking_db, agent_type_id=aid, model_id=mid, golden_id=gid, tag="cand"
        )

    result = _compare_runner().compare_experiments(
        baseline="base", candidate="cand", agent_type="cmp_all_errored_agent"
    )
    assert result["pairs"] == []
    assert result["baseline_only"] == [g1, g2]
    assert result["candidate_runs"] == 0


def test_compare_experiments_raises_when_an_unscored_candidate_replayed_other_goldens(
    tracking_db,
):
    from pw_foundation.exceptions import PreconditionError

    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_errored_elsewhere_agent")
    g1, g2 = (_seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid) for _ in range(2))
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g1, tag="base", score=0.9
    )
    _seed_unscored_replay(tracking_db, agent_type_id=aid, model_id=mid, golden_id=g2, tag="cand")

    with pytest.raises(PreconditionError, match="share no golden parent"):
        _compare_runner().compare_experiments(
            baseline="base", candidate="cand", agent_type="cmp_errored_elsewhere_agent"
        )


def test_compare_experiments_raises_when_tags_share_no_golden(tracking_db):
    """Two populated tags over different golden sets yield zero pairs, which
    must not read as "no regressions"."""
    from pw_foundation.exceptions import PreconditionError

    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_disjoint_agent")
    g1 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    g2 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g1, tag="base", score=0.9
    )
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g2, tag="cand", score=0.9
    )

    with pytest.raises(PreconditionError, match="share no golden parent"):
        _compare_runner().compare_experiments(
            baseline="base", candidate="cand", agent_type="cmp_disjoint_agent"
        )


def test_compare_experiments_rejects_a_self_comparison(tracking_db):
    """Comparing a tag with itself reports every delta as 0.0 — a clean gate
    that proves nothing."""
    from pw_foundation.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError, match="same tag"):
        _compare_runner().compare_experiments(
            baseline="base", candidate="base", agent_type="cmp_self_agent"
        )


def test_compare_experiments_skips_replays_with_a_null_score(tracking_db):
    """A NULL eval_score is not a scored replay; it must be filtered out, not
    crash the float() conversion."""
    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_nullscore_agent")
    g1 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    g2 = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g1, tag="base", score=0.5
    )
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g1, tag="cand", score=0.7
    )
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g2, tag="base", score=None
    )
    _seed_scored_replay(
        tracking_db, agent_type_id=aid, model_id=mid, golden_id=g2, tag="cand", score=None
    )

    result = _compare_runner().compare_experiments(
        baseline="base", candidate="cand", agent_type="cmp_nullscore_agent"
    )
    assert [p["golden_id"] for p in result["pairs"]] == [g1]
    assert result["baseline_runs"] == 1


# ---------------------------------------------------------------------------
# Tag matching is exact regardless of the server's collation
# ---------------------------------------------------------------------------


def test_exact_tag_rows_drops_case_variants():
    """Simulates a case-insensitive collation (MySQL's utf8mb4_0900_ai_ci):
    SQL ``=`` hands back the wrong-case rows, and the filter removes them."""
    from pw_core.eval._experiments import _exact_tag_rows

    rows = [
        {"golden_id": 1, "score": 0.8, "tag": "experiment:v5"},
        {"golden_id": 2, "score": 0.6, "tag": "experiment:V5"},
    ]
    assert [r["golden_id"] for r in _exact_tag_rows(rows, "experiment:v5")] == [1]
    assert [r["golden_id"] for r in _exact_tag_rows(rows, "experiment:V5")] == [2]
    assert _exact_tag_rows(rows, "experiment:V5X") == []


def test_case_variant_candidate_raises_instead_of_reporting_zero_deltas(tracking_db, monkeypatch):
    """On a case-insensitive server the candidate tag selects the baseline's
    own rows, so neither guard fires and an all-zero-delta table reads as
    'no regression' for an experiment that never ran."""
    import pw_core.eval._experiments as ex
    from pw_foundation.exceptions import PreconditionError

    aid, mid = _seed_compare_agent(tracking_db, slug="cmp_case_agent")
    gid = _seed_golden_run(tracking_db, agent_type_id=aid, model_id=mid)
    _seed_scored_replay(
        tracking_db,
        agent_type_id=aid,
        model_id=mid,
        golden_id=gid,
        tag="experiment:v5",
        score=0.8,
    )

    real = ex._fetch_tag_rows
    monkeypatch.setattr(
        ex, "_fetch_tag_rows", lambda repo, tag, agent: real(repo, tag.lower(), agent)
    )

    with pytest.raises(PreconditionError, match="experiment:V5"):
        ex.compare_experiments(
            baseline="experiment:v5",
            candidate="experiment:V5",
            agent_type="cmp_case_agent",
        )
