"""pw_core.config.AppConfig: the framework settings on top of pw-foundation's base."""

from __future__ import annotations

from pw_foundation.config import AppConfig as FoundationConfig

from pw_core.config import AppConfig

FRAMEWORK_DEFAULTS = {
    "DATABASE_URL": "",
    "OPENROUTER_API_KEY": "",
    "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    "OPENROUTER_PROVIDER_IGNORE": [],
    "REDIS_URL": "",
    "WEB_HOST": "127.0.0.1",
    "WEB_PORT": 8000,
    "CORS_ORIGINS": [],
    "REQUEST_TIMEOUT": 120,
    "THREAD_MAX_WORKERS": 4,
    "API_RATE_LIMIT_PER_MINUTE": 60,
    "MAX_PER_PAGE": 200,
    "ID_LENGTH": 12,
}


def test_is_the_foundation_config_plus_framework_fields(monkeypatch):
    for name in list(FRAMEWORK_DEFAULTS) + ["LOG_LEVEL", "LOG_FILE", "APP_NAME", "APP_URL"]:
        monkeypatch.delenv(name, raising=False)
    cfg = AppConfig()
    assert isinstance(cfg, FoundationConfig)
    assert {k: getattr(cfg, k) for k in FRAMEWORK_DEFAULTS} == FRAMEWORK_DEFAULTS
    assert cfg.LOG_LEVEL == "INFO" and cfg.APP_NAME == "App"


def test_framework_fields_resolve_from_env_with_their_types(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "9001")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("OPENROUTER_PROVIDER_IGNORE", "x,y")
    monkeypatch.setenv("REQUEST_TIMEOUT", "not-a-number")
    cfg = AppConfig()
    assert cfg.WEB_PORT == 9001
    assert cfg.CORS_ORIGINS == ["https://a.example", "https://b.example"]
    assert cfg.OPENROUTER_PROVIDER_IGNORE == ["x", "y"]
    assert cfg.REQUEST_TIMEOUT == 120  # malformed env falls back to the default


def test_list_defaults_are_not_shared_between_instances(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    a, b = AppConfig(), AppConfig()
    a.CORS_ORIGINS.append("x")
    assert b.CORS_ORIGINS == []


def test_project_subclass_keeps_both_layers(monkeypatch):
    class Project(AppConfig):
        GREETING: str = "hello"

    monkeypatch.setenv("GREETING", "hi")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    cfg = Project()
    assert cfg.GREETING == "hi" and cfg.DATABASE_URL == "sqlite://" and cfg.LOG_LEVEL == "INFO"
