"""
LLM response cache — exact caching for LLM calls.

Keyed by ``input_hash`` and agent type, with per-agent policy read from
``config/cache.yaml``.

Cache lookup + store happen in the consumer service, not inside the transport
client. ``OpenRouterClient.chat()`` stays transport-only.

Quick start::

    from pw_core.llm.cache import cache_lookup, cache_store, record_cache_hit
    from pw_core.llm.tracking import compute_input_hash

    # In a service function, before the LLM call:
    input_hash = compute_input_hash(
        model=cfg["model"], messages=messages, sampling=cfg,
    )
    hit = cache_lookup(agent_type="classifier", input_hash=input_hash)
    if hit is not None:
        record_cache_hit(hit=hit, duration_ms=1)
        return hit.parsed_output

    # ... make the LLM call ...

    cache_store(
        agent_type="classifier",
        input_hash=input_hash,
        source_run_id=run_id,
        model=cfg["model"],
        parsed_output=parsed,
        raw_response=raw,
        usage=usage,  # refuses the store when finish_reason says truncated
    )

See ``docs/llm-cache.md`` for the full guide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import datetime as dt

from pw_core.llm.cache import _schema as _cache_schema  # noqa: F401 — registers tables
from pw_core.llm.cache._schema import (  # noqa: F401
    ALL_CACHE_TABLES,
    llm_cache_entries,
)
from pw_core.llm.cache.config import (  # noqa: F401
    AgentCacheConfig,
    clear_config_cache,
    get_agent_cache_config,
)
from pw_core.llm.cache.exact import ExactCacheRepo, cache_stats
from pw_core.llm.cache.invalidate import (  # noqa: F401
    by_agent,
    by_model,
    by_run,
    purge_expired,
)
from pw_foundation.log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# CacheHit — returned by cache_lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheHit:
    """Payload returned when a cache lookup succeeds.

    Attributes:
        entry_id: PK of the ``llm_cache_entries`` row.
        parsed_output: The cached parsed JSON value.
        raw_response: The cached raw LLM response string.
        source_run_id: FK to the original ``llm_runs`` row.
        model: Model name used by the source run.
        agent_type: Agent slug.
        input_hash: SHA256 key that produced this hit.
        hit_type: Always ``"exact"``.
        similarity: Always ``1.0``.
        created_at: When the cache entry was created (for age tagging).
    """

    entry_id: int
    parsed_output: Any
    raw_response: str | None
    source_run_id: int
    model: str
    agent_type: str
    input_hash: str
    hit_type: str
    similarity: float
    created_at: dt.datetime | None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def cache_lookup(
    *,
    agent_type: str,
    input_hash: str,
) -> CacheHit | None:
    """Look up a cached response for *agent_type* + *input_hash*.

    Runs only when ``exact=true`` in config.

    Args:
        agent_type: Agent slug used to load policy from ``cache.yaml``.
        input_hash: SHA256 of model + prompts + sampling + configs.
            Computed by :func:`pw_core.llm.tracking.compute_input_hash`.

    Returns:
        A :class:`CacheHit` on success, ``None`` on miss or disabled cache.
    """
    cfg = get_agent_cache_config(agent_type)

    if not cfg.exact:
        return None

    repo = ExactCacheRepo()
    row = repo.lookup(input_hash=input_hash, agent_type=agent_type)
    if row is None:
        if cfg.on_miss == "warn_log":
            logger.warning("cache_miss", agent_type=agent_type)
        return None

    return CacheHit(
        entry_id=row["id"],
        parsed_output=row["parsed_output"],
        raw_response=row["raw_response"],
        source_run_id=row["source_run_id"],
        model=row["model"],
        agent_type=row["agent_type"],
        input_hash=input_hash,
        hit_type="exact",
        similarity=1.0,
        created_at=row.get("created_at"),
    )


def cache_store(
    *,
    agent_type: str,
    input_hash: str,
    source_run_id: int,
    model: str,
    parsed_output: Any = None,
    raw_response: str | None = None,
    usage: dict | None = None,
    truncated: bool | None = None,
) -> None:
    """Store a response in the cache for future lookup.

    No-op when exact caching is disabled for *agent_type*. Refuses the store
    when the response is known-truncated — a cache hit replays stored text
    with no finish reason, so the flag would be lost forever. An explicit
    ``truncated=`` wins; otherwise truncation is derived from ``usage`` via
    :func:`~pw_core.llm.parse.truncated_from_usage`. Unknown truncation
    (neither given) is stored.

    Args:
        agent_type: Agent slug used to load TTL policy.
        input_hash: SHA256 key (from :func:`~pw_core.llm.tracking.compute_input_hash`).
        source_run_id: FK to the ``llm_runs`` row that produced this response.
        model: Model name (stored for attribution on cache-hit runs).
        parsed_output: Parsed JSON to cache.
        raw_response: Raw LLM response string to cache.
        usage: The client ``usage`` dict; its ``finish_reason`` decides
            truncation when ``truncated`` is not given.
        truncated: Explicit truncation flag; ``True`` skips the store
            (logged as ``cache_store_skipped_truncated``).
    """
    cfg = get_agent_cache_config(agent_type)

    if not cfg.exact:
        return

    if truncated is None and usage is not None:
        # Lazy: parse needs the [validate] extra; the cache alone must not.
        from pw_core.llm.parse import truncated_from_usage

        truncated = truncated_from_usage(usage)
    if truncated is True:
        logger.info("cache_store_skipped_truncated", agent_type=agent_type)
        return

    repo = ExactCacheRepo()
    repo.store(
        input_hash=input_hash,
        agent_type=agent_type,
        model=model,
        source_run_id=source_run_id,
        parsed_output=parsed_output,
        raw_response=raw_response,
        ttl_seconds=cfg.ttl_seconds,
    )


def record_cache_hit(
    *,
    hit: CacheHit,
    duration_ms: int = 1,
) -> int:
    """Record a cache hit as a zero-cost ``llm_runs`` row.

    See :mod:`pw_core.llm.cache._recorder` for implementation details.

    Returns:
        The new ``llm_runs.id`` for the cache-hit row.
    """
    from pw_core.llm.cache._recorder import record_cache_hit as _record

    return _record(hit=hit, duration_ms=duration_ms)


__all__ = [
    # Schema
    "ALL_CACHE_TABLES",
    "llm_cache_entries",
    # Config
    "AgentCacheConfig",
    "clear_config_cache",
    "get_agent_cache_config",
    # Data types
    "CacheHit",
    # High-level helpers
    "cache_lookup",
    "cache_stats",
    "cache_store",
    "record_cache_hit",
    # Repos
    "ExactCacheRepo",
    # Invalidation
    "by_agent",
    "by_model",
    "by_run",
    "purge_expired",
]
