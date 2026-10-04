"""pf-core's own YAML config loaders refuse a repeated key instead of keeping the last one."""

from __future__ import annotations

import logging

import pytest

from pf_core.exceptions import ConfigurationError

REPEATED_AGENT = "agents:\n  summarizer:\n    model: model-a\n  summarizer:\n    model: model-b\n"


def test_app_config_yaml(tmp_path):
    from pf_core.config import AppConfig

    path = tmp_path / "app.yaml"
    path.write_text("LOG_LEVEL: INFO\nLOG_LEVEL: DEBUG\n")
    with pytest.raises(ConfigurationError, match="found duplicate key 'LOG_LEVEL'"):
        AppConfig(yaml_file=path)


def test_model_router(tmp_path, monkeypatch):
    from pf_core.llm import _router_loader

    path = tmp_path / "model_router.yaml"
    path.write_text(REPEATED_AGENT)
    monkeypatch.setenv("MODEL_ROUTER_CONFIG", str(path))
    _router_loader.clear_cache()
    try:
        with pytest.raises(ConfigurationError, match="found duplicate key 'summarizer'"):
            _router_loader.load(force=True)
    finally:
        _router_loader.clear_cache()


def test_prompt_file(tmp_path):
    from pf_core.llm.prompts import load_prompts

    path = tmp_path / "prompts.yaml"
    path.write_text("summarizer:\n  system: a\n  user: b\nsummarizer:\n  system: c\n  user: d\n")
    with pytest.raises(ConfigurationError, match="found duplicate key 'summarizer'"):
        load_prompts(path)


def test_eval_config(tmp_path):
    from pf_core.eval._config import clear_config_cache, load_eval_config

    path = tmp_path / "eval.yaml"
    path.write_text(
        "agents:\n  summarizer:\n    pass_threshold: 0.8\n  summarizer:\n    pass_threshold: 0.5\n"
    )
    clear_config_cache()
    try:
        with pytest.raises(ConfigurationError, match="found duplicate key 'summarizer'"):
            load_eval_config(str(path))
    finally:
        clear_config_cache()


def test_budget_config(tmp_path, monkeypatch):
    from pf_core.budget.config import clear_config_cache, load_yaml

    path = tmp_path / "budgets.yaml"
    path.write_text("agents:\n  summarizer:\n    daily: 10.0\n  summarizer:\n    daily: 50.0\n")
    monkeypatch.setenv("BUDGET_CONFIG", str(path))
    clear_config_cache()
    try:
        with pytest.raises(ConfigurationError, match="found duplicate key 'summarizer'"):
            load_yaml()
    finally:
        clear_config_cache()


def test_cache_config_logs_and_turns_caching_off(tmp_path, monkeypatch, caplog):
    from pf_core.llm.cache.config import clear_config_cache, get_agent_cache_config

    path = tmp_path / "cache.yaml"
    path.write_text(
        "defaults:\n  exact: false\n"
        "agents:\n  summarizer:\n    ttl_seconds: 60\n  summarizer:\n    ttl_seconds: 120\n"
    )
    monkeypatch.setenv("CACHE_CONFIG", str(path))
    clear_config_cache()
    try:
        with caplog.at_level(logging.WARNING, logger="pf_core.llm.cache.config"):
            cfg = get_agent_cache_config("classifier")
    finally:
        clear_config_cache()
    assert (cfg.exact, cfg.semantic) == (False, False)
    assert any("cache_config_load_failed" in r.getMessage() for r in caplog.records)
