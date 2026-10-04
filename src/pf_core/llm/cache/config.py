"""
LLM cache configuration loader.

Reads ``CACHE_CONFIG`` env var (default ``config/cache.yaml``) and returns
per-agent policies merged with global defaults. The loaded config is cached
in-process with TTL reload controlled by ``CACHE_CONFIG_RELOAD_SECONDS``.

Usage::

    from pf_core.llm.cache.config import get_agent_cache_config, AgentCacheConfig

    cfg: AgentCacheConfig = get_agent_cache_config("classifier")
    if cfg.exact:
        hit = cache_lookup(...)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pf_core.log import get_logger
from pf_core.utils.env import resolve_int
from pf_core.utils.reload_cache import ReloadCache
from pf_core.utils.yaml import safe_load

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCacheConfig:
    """Per-agent cache policy resolved from cache.yaml."""

    exact: bool = True
    semantic: bool = False
    ttl_seconds: int = 86400
    semantic_threshold: float = 0.93
    semantic_embedding_model: str = ""
    canonicalize: dict[str, bool] = field(default_factory=dict)
    on_miss: str = "proceed"  # 'proceed' | 'warn_log'


_DEFAULTS = AgentCacheConfig()


# ---------------------------------------------------------------------------
# Loader with in-process TTL cache
# ---------------------------------------------------------------------------

# CACHE_CONFIG values meaning "no cache, no file needed" (test suites,
# cache-less deploys).
_DISABLED_SENTINELS = frozenset({"off", "disabled", "none", "0"})

# Recognised but inert.
_IGNORED_KEYS = frozenset({"max_entries_per_agent"})

_FLAGS = ("exact", "semantic")
_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0"})

# Survives reloads, so each inert key is warned about once per process.
_warned_ignored_keys: set[str] = set()


def _warn_ignored_keys(raw: dict[str, Any]) -> None:
    """Log ``cache_config_ignored_key`` once per process for each dead key present."""
    agents = raw.get("agents")
    sections = [raw.get("defaults") or {}, *(agents.values() if isinstance(agents, dict) else [])]
    for section in sections:
        if not isinstance(section, dict):
            continue
        for key in sorted(_IGNORED_KEYS & set(section)):
            if key not in _warned_ignored_keys:
                _warned_ignored_keys.add(key)
                logger.warning("cache_config_ignored_key", key=key)


def _mapping(value: Any) -> dict[str, Any] | None:
    """A section as a mapping: ``{}`` when absent or empty, ``None`` when it is not one."""
    if value is None:
        return {}
    return value if isinstance(value, dict) else None


def _flag(value: Any) -> bool | None:
    """A cache.yaml boolean, quoted or not; ``None`` when it is not one."""
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    return True if word in _TRUE_WORDS else False if word in _FALSE_WORDS else None


def _warn_unusable_values(raw: dict[str, Any]) -> None:
    """Log each section that is not a mapping and each flag that is not a boolean."""
    agents = _mapping(raw.get("agents"))
    sections = [("defaults", raw.get("defaults"))]
    if agents is None:
        logger.warning("cache_config_section_not_a_mapping", section="agents")
    else:
        sections += [(f"agents.{name}", value) for name, value in agents.items()]
    for name, value in sections:
        section = _mapping(value)
        if section is None:
            logger.warning("cache_config_section_not_a_mapping", section=name)
            continue
        for key in _FLAGS:
            if key in section and _flag(section[key]) is None:
                logger.warning(
                    "cache_config_invalid_flag", section=name, key=key, value=str(section[key])
                )


def _reload_seconds() -> int:
    return resolve_int(None, "CACHE_CONFIG_RELOAD_SECONDS", default=60)


def _caching_off() -> dict[str, Any]:
    return {"defaults": {"exact": False, "semantic": False}}


def _load_config(config_path: str) -> dict[str, Any]:
    """Never-raise loader: a missing file yields ``{}`` (framework defaults); a file that
    exists but cannot be used turns caching off, since its own settings may be what
    disabled it."""
    if config_path.strip().lower() in _DISABLED_SENTINELS:
        return _caching_off()

    path = Path(config_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    if not path.exists():
        return {}

    try:
        with open(path) as fh:
            raw = safe_load(fh) or {}
    except Exception as exc:
        logger.warning("cache_config_load_failed", path=str(path), error=str(exc))
        return _caching_off()

    if not isinstance(raw, dict):
        logger.warning("cache_config_not_a_mapping", path=str(path), got=type(raw).__name__)
        return _caching_off()

    logger.debug("cache_config_loaded", path=str(path))
    _warn_ignored_keys(raw)
    _warn_unusable_values(raw)
    return raw


_cache: ReloadCache[str, dict[str, Any]] = ReloadCache(loader=_load_config, ttl=_reload_seconds)


def _build_config(raw: dict[str, Any]) -> AgentCacheConfig:
    """Build an AgentCacheConfig from a dict, filling missing keys with defaults."""
    return AgentCacheConfig(
        exact=_flag(raw.get("exact", _DEFAULTS.exact)) is True,
        semantic=_flag(raw.get("semantic", _DEFAULTS.semantic)) is True,
        ttl_seconds=int(raw.get("ttl_seconds", _DEFAULTS.ttl_seconds)),
        semantic_threshold=float(raw.get("semantic_threshold", _DEFAULTS.semantic_threshold)),
        semantic_embedding_model=str(
            raw.get("semantic_embedding_model", _DEFAULTS.semantic_embedding_model)
        ),
        canonicalize=dict(raw.get("canonicalize", {})),
        on_miss=str(raw.get("on_miss", _DEFAULTS.on_miss)),
    )


def get_agent_cache_config(agent_type: str) -> AgentCacheConfig:
    """Return resolved cache policy for *agent_type*.

    Merges per-agent overrides (from ``agents.<agent_type>`` in cache.yaml)
    on top of global ``defaults``. Falls back to framework defaults when the
    config file is absent or the agent has no entry. A section that is not a
    mapping, or a flag that is not a boolean, turns that caching off.

    Args:
        agent_type: The agent slug (e.g. ``"classifier"``).

    Returns:
        A frozen :class:`AgentCacheConfig` with all fields populated.
    """
    raw = _cache.get(os.environ.get("CACHE_CONFIG", "config/cache.yaml"))

    defaults = _mapping(raw.get("defaults"))
    agents = _mapping(raw.get("agents"))
    overrides = None if agents is None else _mapping(agents.get(agent_type))
    if defaults is None or overrides is None:
        return _build_config(_caching_off()["defaults"])
    return _build_config({**defaults, **overrides})


def clear_config_cache() -> None:
    """Reset the in-process config cache and the once-per-process warned-key set."""
    _cache.clear()
    _warned_ignored_keys.clear()
