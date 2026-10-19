"""pw-core's own utility modules (the foundation set — io, env, hashing, slugify, dates, ids,
json, yaml, throttle, config_path, periods, reload_cache — is ``pw_foundation.utils``).

The pure-stdlib ones are re-exported eagerly below. The URL-fetching
helpers (``url_liveness``, ``urls``) depend on httpx ([http] extra), so they
are re-exported **lazily** via :pep:`562` ``__getattr__`` — importing
``pw_core.utils`` (or any submodule like ``pw_core.utils.similarity``) must not
drag httpx into the dependency-light foundation install. Accessing one of the
lazy names triggers the import, which raises a friendly error if [http] is
missing.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# Eager: pure-stdlib re-exports, no third-party deps.
from pw_core.utils.similarity import (  # noqa: F401
    is_near_duplicate,
    jaccard,
    shingle,
)
from pw_core.utils.url_html import extract_article_metadata  # noqa: F401
from pw_core.utils.url_parse import (  # noqa: F401
    archive_timestamp_is_round,
    canonical_url,
    domain_of,
    extract_path_date,
)
from pw_core.utils.vocab import SlugNormalizer  # noqa: F401

# Lazy: these names live in httpx-backed modules ([http] extra). Mapping name
# -> submodule it lives in.
_LAZY: dict[str, str] = {
    "CacheBackend": "pw_core.utils.url_liveness",
    "check_url": "pw_core.utils.urls",
    "check_url_cached": "pw_core.utils.url_liveness",
    "fetch_url_content": "pw_core.utils.urls",
    "wayback_exists_at": "pw_core.utils.urls",
}


def __getattr__(name: str) -> object:
    """PEP 562 hook: import httpx-backed re-exports on first access."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)  # may raise friendly ImportError
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY])


if TYPE_CHECKING:  # keep static analysers / IDEs aware of the lazy names
    from pw_core.utils.url_liveness import CacheBackend, check_url_cached  # noqa: F401
    from pw_core.utils.urls import (  # noqa: F401
        check_url,
        fetch_url_content,
        wayback_exists_at,
    )
