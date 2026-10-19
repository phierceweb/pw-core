"""pw-core's part of the ``pw-guards`` framework check.

pw-foundation loads this module when pw-core is installed: ``RULES`` join its own
(the HTTP libraries pw-core wraps) and ``DB_MODULES`` tell the layering check which
imports an orchestrator may not make.
"""

from __future__ import annotations

from pw_foundation.guards.framework_rules import FrameworkRule

_HTTP_USE = "pw_core.fetch for downloads, pw_core.clients for LLM and search APIs"
_HTTP_WHY = "timeouts, retries, pacing and an SSRF guard, already written and tested"

RULES: tuple[FrameworkRule, ...] = tuple(
    FrameworkRule(name=name, kind="import", use=_HTTP_USE, why=_HTTP_WHY)
    for name in ("requests", "httpx", "aiohttp", "urllib3")
)

DB_MODULES: tuple[str, ...] = ("pw_core.db", "pw_core.db.connection")

__all__ = ["DB_MODULES", "RULES"]
