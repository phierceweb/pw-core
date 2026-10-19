"""Friendly errors for missing optional-dependency extras.

The foundation install (``pip install pw-core``) is dependency-light: it does
not ship httpx, pydantic, json-repair, tenacity, or typer. Modules that need
those live behind opt-in extras ([http], [llm], [cli], [jobs], ...). When such
a module is imported without its extra installed, the bare third-party
``ImportError`` ("No module named 'json_repair'") is opaque. This helper turns
it into a message that names the extra and the exact pip command.

Usage at the top of a gated leaf module::

    try:
        import httpx
    except ImportError as e:  # pragma: no cover - exercised by bare-install CI
        from pw_core._extras import extra_import_error

        raise extra_import_error(
            "llm", "httpx", feature="pw_core.clients.openrouter"
        ) from e
"""

from __future__ import annotations

# Extra name -> the pip target a user should install. Anything not listed
# falls back to ``pw-core[<extra>]``.
_INSTALL: dict[str, str] = {
    "http": "pw-core[http]",
    "cli": "pw-core[cli]",
    "validate": "pw-core[validate]",
    "llm": "pw-core[llm]",
    "db": "pw-core[db]",
    "web": "pw-core[web]",
    "jobs": "pw-core[jobs]",
    "tracking": "pw-core[tracking]",
    "eval": "pw-core[eval]",
    "otel": "pw-core[otel]",
    "admin": "pw-core[admin]",
    "articles": "pw-core[articles]",
    "jsonschema": "pw-core[jsonschema]",
    "redis": "pw-core[redis]",
    "ratelimit": "pw-core[ratelimit]",
}


# Module prefix -> the extra needed to import it. Longest prefix wins, so a
# subpackage may sit above its parent. Unlisted means base install: no extras.
# Enforced against the source tree by tests/test_extras_tiers.py.
_MODULE_EXTRA: dict[str, str] = {
    "pw_core.alembic": "db",
    "pw_core.budget._aggregate": "tracking",
    "pw_core.budget._schema": "tracking",
    "pw_core.budget.audit": "tracking",
    "pw_core.budget.repo": "tracking",
    "pw_core.budget.scheduler": "tracking",
    "pw_core.budget.snapshot_job": "tracking",
    "pw_core.cli": "cli",
    "pw_core.cli.export": "otel",
    "pw_core.cli.jobs": "jobs",
    "pw_core.clients.anthropic": "anthropic",
    "pw_core.clients.brave": "llm",
    "pw_core.clients.openrouter": "llm",
    "pw_core.db": "db",
    "pw_core.eval": "eval",
    # Minimum tier to import, not the extra you'd install for the feature:
    # jobs modules import under [tracking]; only the CLI needs typer.
    "pw_core.jobs": "tracking",
    "pw_core.llm.cache": "tracking",
    "pw_core.llm.parse": "validate",
    "pw_core.llm.step": "tracking",
    "pw_core.llm.tracked": "tracking",
    "pw_core.llm.tracking": "tracking",
    "pw_core.llm.tracking.otel": "otel",
    "pw_core.llm.validate": "validate",
    # [crawl] = [http,articles]: article_fetch imports utils.urls, which needs httpx.
    "pw_core.utils.article_fetch": "crawl",
    "pw_core.utils.phash": "image-phash",
    "pw_core.utils.url_liveness": "http",
    "pw_core.utils.urls": "http",
    "pw_core.web": "web",
    # Both dashboards need [web] + a DB tier; [admin] supplies both.
    "pw_core.web.jobs_admin": "admin",
    "pw_core.web.llm_admin": "admin",
}


def required_extra(module: str) -> str | None:
    """The extra needed to import *module*, or ``None`` if it is base-install.

    Longest matching prefix wins, so ``pw_core.llm.tracking.repo`` resolves to
    ``tracking`` rather than to whatever ``pw_core.llm`` would give.
    """
    best: str | None = None
    best_len = -1
    for prefix, extra in _MODULE_EXTRA.items():
        if (module == prefix or module.startswith(prefix + ".")) and len(prefix) > best_len:
            best, best_len = extra, len(prefix)
    return best


def install_target(extra: str) -> str:
    """Return the ``pip install`` target for an extra (e.g. ``pw-core[llm]``)."""
    return _INSTALL.get(extra, f"pw-core[{extra}]")


def extra_import_error(extra: str, package: str, *, feature: str) -> ImportError:
    """Build an ``ImportError`` that names the missing extra and pip command.

    Args:
        extra: The optional-dependency extra that ships ``package`` (e.g. ``"llm"``).
        package: The third-party import name that failed (e.g. ``"json_repair"``).
        feature: The pw-core module or capability the caller was importing, used
            in the message (e.g. ``"pw_core.llm.parse"``).

    Returns:
        An ``ImportError`` to ``raise ... from`` the original failure.
    """
    return ImportError(
        f"{feature} requires the '{extra}' extra; '{package}' is not installed. "
        f"Install it with:  pip install {install_target(extra)}"
    )
