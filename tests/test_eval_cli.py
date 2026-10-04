"""Tests for the pf-eval console script: exit codes a CI gate can trust."""

from __future__ import annotations

import inspect
import json
import sys
import tomllib
from pathlib import Path

import pytest

from pf_core.eval._config import clear_config_cache
from pf_core.llm.tracking import clear_resolver_caches, metadata

AGENT = "summarizer"
VERSION = "golden_v1"
AGENT_BLOCK = (
    f"agents:\n  {AGENT}:\n    compare: structured_diff\n    pass_threshold: 0.8\n"
    "    parallelism: 1\n"
)


@pytest.fixture(autouse=True)
def _reset():
    clear_resolver_caches()
    clear_config_cache()
    yield
    clear_resolver_caches()
    clear_config_cache()


@pytest.fixture
def tracking_db(pf_engine):
    metadata.create_all(pf_engine)
    yield pf_engine
    metadata.drop_all(pf_engine)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A consumer project root with config/eval.yaml, as the working directory."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "eval.yaml").write_text(AGENT_BLOCK)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return tmp_path


def _golden(prompt: str, output: dict) -> int:
    from pf_core.eval import GoldenSetRepo
    from pf_core.llm.tracking import LlmRunRepo

    run_id = LlmRunRepo().record(
        agent_type=AGENT,
        model="golden-model",
        rendered_prompts=("Be brief.", prompt),
        raw_response=json.dumps(output),
        parsed_output=output,
    )
    GoldenSetRepo().add(run_id, version=VERSION)
    return run_id


class _Client:
    """Answers each replay by its user prompt; an exception value fails that replay."""

    def __init__(self, answers: dict[str, dict | Exception], calls: list[str]) -> None:
        self.answers = answers
        self.calls = calls

    def chat(self, *, messages, model="", **kwargs):
        self.calls.append(model)
        answer = self.answers[messages[-1]["content"]]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer), {"duration_ms": 1}


def _replay_with(monkeypatch, answers: dict[str, dict | Exception]) -> list[str]:
    """Route every replay to a ``_Client``; returns the models it was called with."""
    calls: list[str] = []

    def _resolve(slug, *, backend=None, model_override=None):
        return _Client(answers, calls), {"model": "fake-model"}, "fake"

    monkeypatch.setattr("pf_core.eval._runner.resolve_agent", _resolve)
    return calls


def _no_router(project, monkeypatch) -> None:
    from pf_core.llm.router import clear_cache

    monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(project / "config" / "absent_router.yaml"))
    clear_cache()


def _route(project, monkeypatch, text: str) -> None:
    from pf_core.llm.router import clear_cache

    path = project / "config" / "model_router.yaml"
    path.write_text(text)
    monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(path))
    monkeypatch.setenv("MODEL_ROUTER_RELOAD_SECONDS", "0")
    clear_cache()


def _openrouter_answers(monkeypatch, answers: dict[str, dict | Exception]) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "pf_core.clients.openrouter.get_client", lambda *a, **k: _Client(answers, calls)
    )
    return calls


JUDGE_BLOCK = (
    f"agents:\n  {AGENT}:\n    compare: llm_judge\n    pass_threshold: 0.75\n    parallelism: 1\n"
)


def _judge_scores(monkeypatch, verdicts: dict[str, float | Exception]) -> None:
    """Route the judge to a client that scores each replay by a marker in its output."""

    class _Judge:
        def chat(self, *, messages, model="", **kwargs):
            shown = messages[-1]["content"]
            verdict = next(v for marker, v in verdicts.items() if f'"{marker}"' in shown)
            if isinstance(verdict, Exception):
                raise verdict
            return json.dumps({"score": verdict, "rationale": "-"}), {}

    monkeypatch.setattr(
        "pf_core.eval._judge.resolve_agent",
        lambda slug, **kwargs: (_Judge(), {"model": "judge-model"}, "fake"),
    )


def _pf_eval(*args: str) -> int:
    from pf_core.eval.cli import main

    try:
        main(list(args))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _run(*extra: str) -> int:
    return _pf_eval("run", "--version", VERSION, "--agent-type", AGENT, *extra)


def _compare(baseline: str, candidate: str) -> int:
    return _pf_eval(
        "compare", "--baseline", baseline, "--candidate", candidate, "--agent-type", AGENT
    )


def test_pf_eval_is_a_declared_console_script():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts["pf-eval"] == "pf_core.eval.cli:main"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_exits_0_when_every_golden_passes(tracking_db, project, monkeypatch):
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 0


def test_run_exits_1_when_a_replay_errors(tracking_db, project, monkeypatch):
    _golden("Q1", {"answer": 1})
    _golden("Q2", {"answer": 2})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}, "Q2": TimeoutError("upstream timed out")})
    assert _run() == 1


def test_run_exits_2_when_the_version_has_no_goldens(tracking_db, project):
    assert _pf_eval("run", "--version", "golden_unseeded", "--agent-type", AGENT) == 2


def test_run_writes_the_report_into_a_directory_it_creates(tracking_db, project, monkeypatch):
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--output", "out/report.html") == 0
    assert (project / "out" / "report.html").is_file()


def test_run_exits_2_before_replaying_when_the_report_cannot_be_rendered(
    tracking_db, project, monkeypatch
):
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    monkeypatch.setitem(sys.modules, "jinja2", None)
    assert _run("--output", "report.html") == 2
    assert calls == []


def test_run_exits_2_when_writing_the_report_fails(tracking_db, project, monkeypatch):
    from pf_core.eval import EvalReport

    def _disk_full(self, path):
        raise OSError(28, "No space left on device")

    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    monkeypatch.setattr(EvalReport, "write_html", _disk_full)
    assert _run("--output", "report.html") == 2


def test_the_eval_extra_installs_the_report_renderer():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extra = tomllib.loads(pyproject.read_text())["project"]["optional-dependencies"]["eval"]
    assert any(req.startswith("jinja2") for req in extra)


def test_run_exits_2_for_a_backend_the_agent_does_not_declare(tracking_db, project, monkeypatch):
    _route(
        project, monkeypatch, f"agents:\n  {AGENT}:\n    backends:\n      anthropic: {{model: m}}\n"
    )
    _golden("Q1", {"answer": 1})
    calls = _openrouter_answers(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--target", "backend=antropic") == 2
    assert calls == []


def test_run_exits_2_for_an_unknown_backend(tracking_db, project, monkeypatch):
    _no_router(project, monkeypatch)
    _golden("Q1", {"answer": 1})
    calls = _openrouter_answers(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--target", "backend=antropic", "--target", "model=m") == 2
    assert calls == []


def test_an_agent_absent_from_the_router_replays_on_the_named_backend(
    tracking_db, project, monkeypatch
):
    from pf_core.clients import routing

    _no_router(project, monkeypatch)
    _golden("Q1", {"answer": 1})
    monkeypatch.setattr(routing, "_registry", dict(routing._registry))
    calls: list[str] = []
    routing.register_client("evalproj_backend", lambda **kw: _Client({"Q1": {"answer": 1}}, calls))
    openrouter_calls = _openrouter_answers(monkeypatch, {"Q1": {"answer": 1}})

    assert _run("--target", "backend=evalproj_backend", "--target", "model=m") == 0
    assert calls == ["m"]
    assert openrouter_calls == []


def test_run_exits_2_when_the_router_fails_to_load(tracking_db, project, monkeypatch):
    block = f"  {AGENT}:\n    backends:\n      anthropic: {{model: m}}\n"
    _route(project, monkeypatch, f"agents:\n{block}{block}")
    _golden("Q1", {"answer": 1})
    calls = _openrouter_answers(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--target", "model=m") == 2
    assert calls == []


def test_run_exits_2_when_the_routed_backend_has_no_client(tracking_db, project, monkeypatch):
    _route(
        project,
        monkeypatch,
        f"agents:\n  {AGENT}:\n    backends:\n      unregistered: {{model: m}}\n",
    )
    _golden("Q1", {"answer": 1})
    assert _run() == 2


def test_run_exits_2_when_the_backend_client_cannot_be_built(tracking_db, project, monkeypatch):
    _route(
        project,
        monkeypatch,
        f"agents:\n  {AGENT}:\n    backends:\n      openrouter: {{model: m}}\n",
    )
    monkeypatch.setattr("pf_core.clients.openrouter._client", None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _golden("Q1", {"answer": 1})
    assert _run() == 2


def test_run_exits_2_when_no_model_resolves(tracking_db, project, monkeypatch):
    _no_router(project, monkeypatch)
    _golden("Q1", {"answer": 1})
    calls = _openrouter_answers(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 2
    assert calls == []


def test_run_exits_2_before_replaying_when_the_judge_is_not_routed(
    tracking_db, project, monkeypatch
):
    (project / "config" / "eval.yaml").write_text(
        f"agents:\n  {AGENT}:\n    compare: llm_judge\n    parallelism: 1\n"
    )
    _no_router(project, monkeypatch)
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 2
    assert calls == []


def test_run_exits_1_when_a_judge_call_fails(tracking_db, project, monkeypatch):
    (project / "config" / "eval.yaml").write_text(JUDGE_BLOCK)
    for i in range(5):
        _golden(f"Q{i}", {"answer": i})
    _replay_with(monkeypatch, {f"Q{i}": {"reply": f"R{i}"} for i in range(5)})
    verdicts: dict[str, float | Exception] = {f"R{i}": 1.0 for i in range(4)}
    verdicts["R4"] = TimeoutError("judge rate limited")
    _judge_scores(monkeypatch, verdicts)
    assert _run() == 1


@pytest.mark.parametrize("compare", ["structured_dif", "custom:evalproj_never_registered"])
def test_run_exits_2_before_replaying_for_an_unknown_comparator(
    tracking_db, project, monkeypatch, compare
):
    (project / "config" / "eval.yaml").write_text(
        f"agents:\n  {AGENT}:\n    compare: {compare}\n    parallelism: 1\n"
    )
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 2
    assert calls == []


@pytest.mark.parametrize(
    "pairs",
    [["modle=m"], ["temperature=0.7"], ["model="], ["backend="], ["model=a", "model=b"]],
)
def test_run_exits_2_before_replaying_for_a_target_it_cannot_apply(
    tracking_db, project, monkeypatch, pairs
):
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run(*[arg for pair in pairs for arg in ("--target", pair)]) == 2
    assert calls == []


def test_run_replays_every_golden_past_the_listing_default(tracking_db, project, monkeypatch):
    from pf_core.eval import GoldenSetRepo

    n = inspect.signature(GoldenSetRepo.list).parameters["limit"].default + 1
    for i in range(n):
        _golden(f"Q{i}", {"answer": i})
    calls = _replay_with(monkeypatch, {f"Q{i}": {"answer": i} for i in range(n)})
    assert _run() == 0
    assert len(calls) == n


def test_run_exits_2_for_a_bad_key_in_another_agents_block(tracking_db, project, monkeypatch):
    (project / "config" / "eval.yaml").write_text(
        AGENT_BLOCK + "  classifier:\n    pass_treshold: 0.99\n"
    )
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 2
    assert calls == []


@pytest.mark.parametrize(
    "tag", ["eval:golden_v1", "EVAL:golden_v1", "eval:replay:golden_v1", f"agent:{AGENT}"]
)
def test_run_exits_2_for_a_tag_in_the_eval_namespace(tracking_db, project, monkeypatch, tag):
    from pf_core.eval import GoldenSetRepo

    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--tag-as", tag) == 2
    assert calls == []
    assert len(GoldenSetRepo().list(version=VERSION)) == 1


def test_run_exits_2_for_an_invalid_eval_config(tracking_db, project):
    (project / "config" / "eval.yaml").write_text("defaults:\n  pass_threshold: high\n")
    assert _run() == 2


def test_run_exits_2_before_replaying_when_the_named_config_is_missing(
    tracking_db, project, monkeypatch
):
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--config", "config/eval.yml") == 2
    assert calls == []


def test_run_exits_2_before_replaying_for_an_empty_config_path(tracking_db, project, monkeypatch):
    (project / "config" / "eval.yaml").unlink()
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run("--config", "") == 2
    assert calls == []


def test_run_exits_2_before_replaying_when_eval_config_names_a_missing_file(
    tracking_db, project, monkeypatch
):
    monkeypatch.setenv("EVAL_CONFIG", "config/eval.yml")
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 2
    assert calls == []


def test_run_exits_2_before_replaying_when_eval_yaml_has_no_block_for_the_agent(
    tracking_db, project, monkeypatch
):
    (project / "config" / "eval.yaml").write_text(
        "agents:\n  sumarizer:\n    pass_threshold: 0.99\n"
    )
    _golden("Q1", {"answer": 1})
    calls = _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    assert _run() == 2
    assert calls == []


def test_run_exits_1_when_a_comparator_scores_outside_0_to_1(tracking_db, project, monkeypatch):
    from pf_core.eval import _compare

    monkeypatch.setattr(_compare, "_registry", dict(_compare._registry))
    _compare.register_comparator("evalproj_percent")(
        lambda golden, replay, *, context: 85.0 if replay == golden else 0.0
    )
    (project / "config" / "eval.yaml").write_text(
        f"agents:\n  {AGENT}:\n    compare: evalproj_percent\n    parallelism: 1\n"
    )
    for i in range(5):
        _golden(f"Q{i}", {"answer": i})
    _replay_with(
        monkeypatch, {"Q0": {"answer": 0}, **{f"Q{i}": {"answer": -1} for i in range(1, 5)}}
    )
    assert _run() == 1


def test_run_exits_2_when_the_database_fails(tracking_db, project, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from pf_core.eval import GoldenSetRepo

    def _down(self, **kwargs):
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(GoldenSetRepo, "list", _down)
    assert _run() == 2


def test_run_from_a_project_root_loads_its_dotenv_before_its_imports(
    tracking_db, project, monkeypatch, request
):
    monkeypatch.setenv("EVALPROJ_MARKER", "unset-below")
    monkeypatch.delenv("EVALPROJ_MARKER")
    (project / ".env").write_text("EVALPROJ_MARKER=from-dotenv\n")
    (project / "evalproj_setup.py").write_text(
        "import os\n\nMARKER = os.environ.get('EVALPROJ_MARKER')\n"
    )
    request.addfinalizer(lambda: sys.modules.pop("evalproj_setup", None))
    (project / "config" / "eval.yaml").write_text("imports: [evalproj_setup]\n" + AGENT_BLOCK)
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})

    assert _run() == 0
    assert sys.modules["evalproj_setup"].MARKER == "from-dotenv"


def test_a_shell_variable_wins_over_the_dotenv(tracking_db, project, monkeypatch, request):
    monkeypatch.setenv("EVALPROJ_MARKER", "from-shell")
    (project / ".env").write_text("EVALPROJ_MARKER=from-dotenv\n")
    (project / "evalproj_setup_shell.py").write_text(
        "import os\n\nMARKER = os.environ.get('EVALPROJ_MARKER')\n"
    )
    request.addfinalizer(lambda: sys.modules.pop("evalproj_setup_shell", None))
    (project / "config" / "eval.yaml").write_text("imports: [evalproj_setup_shell]\n" + AGENT_BLOCK)
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})

    assert _run() == 0
    assert sys.modules["evalproj_setup_shell"].MARKER == "from-shell"


@pytest.mark.parametrize(
    "command",
    [
        ["list-golden", "--version", VERSION],
        ["seed", "--version", VERSION, "--outcome-kind", "summary_accepted", "--dry-run"],
        ["promote", "{run_id}", "--version", "golden_v2"],
        ["demote", "{run_id}", "--version", VERSION],
    ],
    ids=["list-golden", "seed", "promote", "demote"],
)
def test_golden_set_commands_run_the_configs_imports(
    tracking_db, project, monkeypatch, request, command
):
    (project / "evalproj_golden_setup.py").write_text("LOADED = True\n")
    request.addfinalizer(lambda: sys.modules.pop("evalproj_golden_setup", None))
    (project / "config" / "eval.yaml").write_text(
        "imports: [evalproj_golden_setup]\n" + AGENT_BLOCK
    )
    run_id = _golden("Q1", {"answer": 1})

    assert _pf_eval(*[arg.format(run_id=run_id) for arg in command]) == 0
    assert "evalproj_golden_setup" in sys.modules


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_compare_exits_0_when_the_candidate_is_no_worse(tracking_db, project, monkeypatch):
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    _run("--tag-as", "experiment:base")
    _run("--tag-as", "experiment:cand")
    assert _compare("experiment:base", "experiment:cand") == 0


def test_compare_exits_1_when_the_candidate_scores_lower(tracking_db, project, monkeypatch):
    _golden("Q1", {"answer": 1, "label": "a"})
    _replay_with(monkeypatch, {"Q1": {"answer": 1, "label": "a"}})
    _run("--tag-as", "experiment:base")
    _replay_with(monkeypatch, {"Q1": {"answer": 1, "label": "b"}})
    _run("--tag-as", "experiment:cand")
    assert _compare("experiment:base", "experiment:cand") == 1


def test_compare_exits_1_when_the_candidate_misses_a_golden_the_baseline_scored(
    tracking_db, project, monkeypatch
):
    _golden("Q1", {"answer": 1})
    _golden("Q2", {"answer": 2})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}, "Q2": {"answer": 2}})
    _run("--tag-as", "experiment:base")
    _replay_with(monkeypatch, {"Q1": {"answer": 1}, "Q2": TimeoutError("upstream timed out")})
    _run("--tag-as", "experiment:cand")
    assert _compare("experiment:base", "experiment:cand") == 1


def test_compare_exits_2_when_a_tag_matches_nothing(tracking_db, project, monkeypatch):
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    _run("--tag-as", "experiment:base")
    assert _compare("experiment:base", "experiment:never-run") == 2


def test_compare_exits_1_when_the_candidate_errored_on_every_golden(
    tracking_db, project, monkeypatch
):
    _golden("Q1", {"answer": 1})
    _golden("Q2", {"answer": 2})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}, "Q2": {"answer": 2}})
    _run("--tag-as", "experiment:base")
    _replay_with(
        monkeypatch, {"Q1": TimeoutError("upstream timed out"), "Q2": TimeoutError("timed out")}
    )
    _run("--tag-as", "experiment:cand")
    assert _compare("experiment:base", "experiment:cand") == 1


def test_compare_exits_1_when_a_rerun_under_the_candidate_tag_errors(
    tracking_db, project, monkeypatch
):
    _golden("Q1", {"answer": 1})
    _replay_with(monkeypatch, {"Q1": {"answer": 1}})
    _run("--tag-as", "experiment:base")
    _run("--tag-as", "experiment:cand")
    _replay_with(monkeypatch, {"Q1": TimeoutError("upstream timed out")})
    _run("--tag-as", "experiment:cand")
    assert _compare("experiment:base", "experiment:cand") == 1


def test_compare_leaves_out_a_golden_the_baseline_judge_failed_on(
    tracking_db, project, monkeypatch
):
    (project / "config" / "eval.yaml").write_text(JUDGE_BLOCK)
    _golden("Q1", {"answer": 1})
    _golden("Q2", {"answer": 2})
    _replay_with(monkeypatch, {"Q1": {"reply": "B1"}, "Q2": {"reply": "B2"}})
    _judge_scores(monkeypatch, {"B1": 0.9, "B2": ConnectionError("judge unreachable")})
    _run("--tag-as", "experiment:base")
    _replay_with(monkeypatch, {"Q1": {"reply": "C1"}, "Q2": {"reply": "C2"}})
    _judge_scores(monkeypatch, {"C1": 0.5, "C2": 0.6})
    _run("--tag-as", "experiment:cand")
    assert _compare("experiment:base", "experiment:cand") == 1


def test_compare_exits_2_when_the_database_fails(tracking_db, project, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def _down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

    monkeypatch.setattr("pf_core.eval._experiments._tag_replays", _down)
    assert _compare("experiment:base", "experiment:cand") == 2
