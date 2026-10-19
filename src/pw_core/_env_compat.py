"""``PW_*`` env vars with the ``PF_*`` spelling honored until 1.0 (one DeprecationWarning each)."""

from __future__ import annotations

import os
import warnings

_warned: set[str] = set()


def env_name(pw_name: str) -> str:
    """The name to read: ``pw_name`` if set, else its ``PF_`` spelling if set, else ``pw_name``."""
    if pw_name in os.environ or not pw_name.startswith("PW_"):
        return pw_name
    legacy = "PF_" + pw_name[3:]
    if legacy in os.environ:
        if legacy not in _warned:
            _warned.add(legacy)
            warnings.warn(
                f"{legacy} is deprecated; set {pw_name}", DeprecationWarning, stacklevel=3
            )
        return legacy
    return pw_name


def getenv(pw_name: str, default: str | None = None) -> str | None:
    return os.environ.get(env_name(pw_name), default)
