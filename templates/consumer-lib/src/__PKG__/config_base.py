"""__NAME__ configuration.

Subclass ``pw_foundation.config.AppConfig`` and add project settings as class
attributes (env var name == attribute name). See ``docs/pw-foundation/config.md``.
"""

from __future__ import annotations

from pathlib import Path

from pw_foundation.config import AppConfig


class Config(AppConfig):
    """Project settings. Add your own as typed class attributes, e.g.::

    GREETING: str = "hello"
    """


_env = Path(".env")
cfg = Config(env_file=_env if _env.exists() else None)
