"""__NAME__ configuration.

Subclass ``pw_core.config.AppConfig``; add settings as typed class attributes
(env var name == attribute name). Built-ins include DATABASE_URL, WEB_HOST,
WEB_PORT, LOG_LEVEL, etc. — see ``docs/pw-core/config.md``.
"""

from __future__ import annotations

from pathlib import Path

from pw_core.config import AppConfig


class Config(AppConfig):
    """Project settings. Add your own as typed class attributes."""


_env = Path(".env")
cfg = Config(env_file=_env if _env.exists() else None)
