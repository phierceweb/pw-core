"""Tests for pw_core.eval._config."""

from __future__ import annotations

import sys
import textwrap
import threading

import pytest

from pw_core.eval._config import (
    AgentEvalConfig,
    EvalConfig,
    clear_config_cache,
    load_eval_config,
)
from pw_foundation.exceptions import ConfigurationError


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_config_cache()
    yield
    clear_config_cache()


# ---------------------------------------------------------------------------
# AgentEvalConfig defaults
# ---------------------------------------------------------------------------


def test_agent_eval_config_defaults():
    cfg = AgentEvalConfig()
    assert cfg.compare == "structured_diff"
    assert cfg.pass_threshold == 0.85
    assert cfg.parallelism == 4
    assert cfg.sampling == {"temperature": 0.0}
    assert cfg.diff_fields == []
    assert cfg.tolerances == {}
    assert cfg.judge_agent_type is None
    assert cfg.metrics == []


def test_agent_eval_config_custom():
    cfg = AgentEvalConfig(compare="llm_judge", pass_threshold=0.9, judge_agent_type="my_judge")
    assert cfg.compare == "llm_judge"
    assert cfg.pass_threshold == 0.9
    assert cfg.judge_agent_type == "my_judge"


# ---------------------------------------------------------------------------
# EvalConfig from raw dict
# ---------------------------------------------------------------------------


def test_eval_config_empty():
    cfg = EvalConfig({})
    agent_cfg = cfg.for_agent("drafter")
    assert agent_cfg.compare == "structured_diff"
    assert agent_cfg.pass_threshold == 0.85


def test_eval_config_defaults_propagate():
    cfg = EvalConfig(
        {
            "defaults": {"pass_threshold": 0.70, "parallelism": 2},
            "agents": {},
        }
    )
    agent_cfg = cfg.for_agent("unknown_agent")
    assert agent_cfg.pass_threshold == 0.70
    assert agent_cfg.parallelism == 2


def test_eval_config_agent_overrides_defaults():
    cfg = EvalConfig(
        {
            "defaults": {"pass_threshold": 0.80, "compare": "structured_diff"},
            "agents": {
                "drafter": {
                    "compare": "llm_judge",
                    "judge_agent_type": "drafter_judge",
                    "pass_threshold": 0.75,
                }
            },
        }
    )
    drafter_cfg = cfg.for_agent("drafter")
    assert drafter_cfg.compare == "llm_judge"
    assert drafter_cfg.judge_agent_type == "drafter_judge"
    assert drafter_cfg.pass_threshold == 0.75


def test_for_agent_refuses_an_agent_the_agents_section_does_not_list():
    """A misspelled block would otherwise leave the run on the defaults' looser threshold."""
    cfg = EvalConfig({"agents": {"sumarizer": {"pass_threshold": 0.99}}})
    with pytest.raises(ConfigurationError, match=r"agents\.summarizer.*sumarizer"):
        cfg.for_agent("summarizer")


@pytest.mark.parametrize("text", ["agents:\n  on: {}\n", "agents:\n  1: {}\n"])
def test_an_agents_key_yaml_reads_as_a_non_string_is_refused(text):
    import yaml

    with pytest.raises(ConfigurationError, match="quote"):
        EvalConfig(yaml.safe_load(text))


def test_for_agent_reads_an_empty_block_as_the_defaults():
    cfg = EvalConfig(
        {"defaults": {"pass_threshold": 0.70}, "agents": {"drafter": None, "classifier": {}}}
    )
    assert cfg.for_agent("drafter").pass_threshold == 0.70
    assert cfg.for_agent("classifier").pass_threshold == 0.70


def test_eval_config_metrics_gates():
    cfg = EvalConfig(
        {
            "agents": {
                "drafter": {
                    "metrics": [
                        {"name": "tier1_ratio", "min": 0.70},
                        {"name": "n_sources", "min": 3, "max": 20},
                    ]
                }
            }
        }
    )
    drafter_cfg = cfg.for_agent("drafter")
    assert len(drafter_cfg.metrics) == 2
    assert drafter_cfg.metrics[0].name == "tier1_ratio"
    assert drafter_cfg.metrics[0].min == 0.70
    assert drafter_cfg.metrics[1].max == 20


def test_eval_config_diff_fields_and_tolerances():
    cfg = EvalConfig(
        {
            "agents": {
                "reviewer": {
                    "diff_fields": ["score", "category"],
                    "tolerances": {"score": 3.0},
                }
            }
        }
    )
    reviewer_cfg = cfg.for_agent("reviewer")
    assert reviewer_cfg.diff_fields == ["score", "category"]
    assert reviewer_cfg.tolerances == {"score": 3.0}


# ---------------------------------------------------------------------------
# load_eval_config — file loading
# ---------------------------------------------------------------------------


def test_load_eval_config_missing_default_file(tmp_path, monkeypatch):
    """No config/eval.yaml and no path named → the framework defaults."""
    monkeypatch.delenv("EVAL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load_eval_config()
    assert isinstance(cfg, EvalConfig)
    assert cfg.for_agent("any").compare == "structured_diff"


def test_load_eval_config_named_path_must_exist(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_CONFIG", raising=False)
    with pytest.raises(ConfigurationError, match="eval.yml.*does not exist"):
        load_eval_config(str(tmp_path / "eval.yml"))


def test_a_missing_default_does_not_answer_for_a_named_path(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    load_eval_config()
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_eval_config("config/eval.yaml")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "eval.yaml").write_text("defaults:\n  pass_threshold: 0.99\n")
    assert load_eval_config().defaults.pass_threshold == 0.99


@pytest.mark.parametrize("path", ["", "   "])
def test_load_eval_config_refuses_an_empty_path(tmp_path, monkeypatch, path):
    monkeypatch.delenv("EVAL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError, match="empty"):
        load_eval_config(path)


def test_load_eval_config_env_path_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_CONFIG", str(tmp_path / "nonexistent.yaml"))
    with pytest.raises(ConfigurationError, match="EVAL_CONFIG.*does not exist"):
        load_eval_config()


def test_load_eval_config_from_file(tmp_path, monkeypatch):
    yaml_content = textwrap.dedent("""\
        defaults:
          pass_threshold: 0.60
        agents:
          drafter:
            compare: llm_judge
            judge_agent_type: drafter_judge
    """)
    config_file = tmp_path / "eval.yaml"
    config_file.write_text(yaml_content)
    monkeypatch.setenv("EVAL_CONFIG", str(config_file))

    cfg = load_eval_config()
    assert cfg.defaults.pass_threshold == 0.60
    drafter = cfg.for_agent("drafter")
    assert drafter.compare == "llm_judge"
    assert drafter.judge_agent_type == "drafter_judge"


def test_load_eval_config_cached(tmp_path, monkeypatch):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("defaults:\n  pass_threshold: 0.77\n")
    monkeypatch.setenv("EVAL_CONFIG", str(config_file))

    cfg1 = load_eval_config()
    cfg2 = load_eval_config()
    assert cfg1 is cfg2  # same object from cache


def test_load_eval_config_invalid_yaml(tmp_path, monkeypatch):
    from pw_foundation.exceptions import ConfigurationError

    config_file = tmp_path / "eval.yaml"
    config_file.write_text(":\ninvalid: [unclosed\n")
    monkeypatch.setenv("EVAL_CONFIG", str(config_file))

    with pytest.raises(ConfigurationError):
        load_eval_config()


# ---------------------------------------------------------------------------
# imports: project modules loaded with the config
# ---------------------------------------------------------------------------


def test_imports_register_project_comparators(tmp_path, monkeypatch, request):
    from pw_core.eval import _compare

    monkeypatch.setattr(_compare, "_registry", dict(_compare._registry))
    (tmp_path / "evalproj_comparators.py").write_text(
        "from pw_core.eval import register_comparator\n\n"
        "@register_comparator('evalproj_count_ratio')\n"
        "def count_ratio(golden, replay, *, context):\n"
        "    return 1.0\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    request.addfinalizer(lambda: sys.modules.pop("evalproj_comparators", None))
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("imports: [evalproj_comparators]\n")

    load_eval_config(str(config_file))

    assert _compare.get_comparator("evalproj_count_ratio") is not None


def test_an_import_that_fails_is_a_configuration_error(tmp_path):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("imports: [evalproj_no_such_module]\n")
    with pytest.raises(ConfigurationError, match="evalproj_no_such_module"):
        load_eval_config(str(config_file))


def test_imports_must_be_a_list_of_module_names(tmp_path):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("imports: evalproj_comparators\n")
    with pytest.raises(ConfigurationError, match="imports"):
        load_eval_config(str(config_file))


def test_an_imported_module_may_load_the_eval_config(tmp_path, monkeypatch, request):
    # A deadlocked load holds this lock forever; a throwaway keeps the rest of the suite running.
    monkeypatch.setattr("pw_core.eval._config._lock", threading.Lock())
    config_file = tmp_path / "eval.yaml"
    (tmp_path / "evalproj_reentrant.py").write_text(
        f"from pw_core.eval import load_eval_config\n\nCFG = load_eval_config({str(config_file)!r})\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    request.addfinalizer(lambda: sys.modules.pop("evalproj_reentrant", None))
    config_file.write_text("imports: [evalproj_reentrant]\n")

    loaded = []
    worker = threading.Thread(
        target=lambda: loaded.append(load_eval_config(str(config_file))), daemon=True
    )
    worker.start()
    worker.join(timeout=10)

    assert loaded, "load_eval_config never returned"


# ---------------------------------------------------------------------------
# Malformed config is a ConfigurationError
# ---------------------------------------------------------------------------


def test_a_config_that_is_not_a_mapping_is_a_configuration_error(tmp_path):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("- summarizer\n")
    with pytest.raises(ConfigurationError, match="mapping"):
        load_eval_config(str(config_file))


def test_an_invalid_default_is_a_configuration_error(tmp_path):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("defaults:\n  pass_threshold: high\n")
    with pytest.raises(ConfigurationError, match="pass_threshold"):
        load_eval_config(str(config_file))


def test_an_invalid_agent_block_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="summarizer"):
        EvalConfig({"agents": {"summarizer": {"parallelism": "many"}}})


def test_a_misspelled_default_is_a_configuration_error(tmp_path):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("defaults:\n  pass_treshold: 0.99\n")
    with pytest.raises(ConfigurationError, match="pass_treshold"):
        load_eval_config(str(config_file))


def test_a_misspelled_agent_key_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="diff_feilds"):
        EvalConfig({"agents": {"summarizer": {"diff_feilds": ["label"]}}})


@pytest.mark.parametrize(
    ("block", "match"),
    [("    pass_treshold: 0.99\n", "pass_treshold"), ("    - summarizer\n", "must be a mapping")],
)
def test_every_agent_block_is_validated_when_the_config_loads(tmp_path, block, match):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text(
        "agents:\n  summarizer:\n    pass_threshold: 0.8\n  classifier:\n" + block
    )
    with pytest.raises(ConfigurationError, match=rf"(?s)agents\.classifier.*{match}"):
        load_eval_config(str(config_file))


@pytest.mark.parametrize("key", ["model", "backend"])
def test_sampling_may_not_choose_the_model_or_backend(key):
    with pytest.raises(ConfigurationError, match=key):
        EvalConfig({"defaults": {"sampling": {"temperature": 0.0, key: "x"}}})


def test_a_misspelled_metric_gate_key_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="mx"):
        EvalConfig({"defaults": {"metrics": [{"name": "latency_ms", "mx": 500}]}})


def test_an_unknown_top_level_key_is_a_configuration_error(tmp_path):
    config_file = tmp_path / "eval.yaml"
    config_file.write_text("agent:\n  summarizer:\n    pass_threshold: 0.99\n")
    with pytest.raises(ConfigurationError, match="'agent'"):
        load_eval_config(str(config_file))
