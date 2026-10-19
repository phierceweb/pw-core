"""Framework settings for pw-core consumers.

``AppConfig`` extends :class:`pw_foundation.config.AppConfig` — ``LOG_LEVEL``, ``LOG_FILE``,
``APP_NAME``, ``APP_URL``, the env > YAML > class-default resolution and ``.env`` loading —
with the settings pw-core's database, LLM and web layers read. Subclass it in each project
and add typed class attributes (env var name == attribute name).
"""

from __future__ import annotations

from pw_foundation.config import AppConfig as _FoundationConfig


class AppConfig(_FoundationConfig):
    """Foundation settings plus the framework's."""

    DATABASE_URL: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_PROVIDER_IGNORE: list[str] = []
    REDIS_URL: str = ""
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8000
    CORS_ORIGINS: list[str] = []
    REQUEST_TIMEOUT: int = 120
    THREAD_MAX_WORKERS: int = 4
    API_RATE_LIMIT_PER_MINUTE: int = 60
    MAX_PER_PAGE: int = 200
    ID_LENGTH: int = 12


__all__ = ["AppConfig"]
