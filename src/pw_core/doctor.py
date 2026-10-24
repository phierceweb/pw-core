"""pw-core's rows in the ``pw-doctor`` report.

pw-foundation's ``pw-doctor`` loads :func:`checks` when pw-core is installed and appends the
rows to its own (copy, interpreter, env, wiring). Read-only: nothing here writes, migrates or
imports consumer application code.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from pathlib import Path

from pw_foundation.doctor import CheckResult, redact_value

from pw_core._env_compat import env_name

_PROBE_CONNECT_TIMEOUT_S = 5

# Env vars pw-core features read, reported by the `env` check.
_ENV_VARS = (
    "DATABASE_URL",
    "MODEL_ROUTER_CONFIG",
    "REDIS_URL",
    "CACHE_CONFIG",
    "WEB_HOST",
    "WEB_PORT",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "BRAVE_API_KEY",
    "REQUEST_TIMEOUT",
    # Safety switches — surface when outbound HTTP runs unverified or unguarded.
    "PW_VERIFY_TLS",
    "URL_CHECK_VERIFY_TLS",
    "URL_FETCH_ALLOW_PRIVATE",
)

# Marker import -> extra it indicates (find_spec only; nothing is imported).
_EXTRA_MARKERS = (
    ("httpx", "http"),
    ("pydantic", "validate"),
    ("json_repair", "validate"),
    ("typer", "cli"),
    ("tenacity", "llm"),
    ("sqlalchemy", "db"),
    ("alembic", "db"),
    ("fastapi", "web"),
    ("anthropic", "anthropic"),
    ("redis", "redis"),
    ("slowapi", "ratelimit"),
    ("trafilatura", "articles"),
    ("jsonschema", "jsonschema"),
    ("PIL", "image-phash"),
)

_DEP_VERSIONS = (
    "anthropic",
    "httpx",
    "pydantic",
    "sqlalchemy",
    "fastapi",
    "structlog",
    "tenacity",
    "typer",
)

# ---------------------------------------------------------------------------
# Core checks — local, read-only, no network.


def check_extras() -> list[CheckResult]:
    installed = sorted(
        {extra for module, extra in _EXTRA_MARKERS if importlib.util.find_spec(module)}
    )
    detail = ", ".join(installed) if installed else "none (foundation only)"
    return [CheckResult("extras", "available", "PASS", detail)]


def check_env() -> list[CheckResult]:
    # Load the consumer's .env first so the report shows the EFFECTIVE env
    # the app would boot with, not just the bare shell. Existing shell vars
    # win (dotenv default), matching consumer boot behavior.
    dotenv_path = Path.cwd() / ".env"
    source = "shell only (no .env in cwd)"
    if dotenv_path.is_file():
        from dotenv import load_dotenv

        load_dotenv(dotenv_path)
        source = f".env loaded from {dotenv_path}"
    lines = [source]
    warn = False
    for var in _ENV_VARS:
        read_from = env_name(var)
        value = os.environ.get(read_from, "")
        hidden = "PF_" + var[3:] if var.startswith("PW_") else ""
        if read_from == var and not value:
            if var not in os.environ:
                lines.append(f"{var} unset")
            elif hidden and os.environ.get(hidden):
                warn = True
                lines.append(f"{var}= (set empty, {hidden} ignored)")
            else:
                lines.append(f"{var}= (set empty)")
            continue
        line = f"{var}={redact_value(var, value)}"
        if read_from != var:
            warn = True
            line += f" (read from {read_from}, deprecated)"
        lines.append(line)
    return [CheckResult("env", "pw-core", "WARN" if warn else "PASS", "; ".join(lines))]


def check_router() -> list[CheckResult]:
    from pw_core.llm import _router_loader

    path = _router_loader.config_path()
    if not Path(path).is_file():
        return [CheckResult("router", "config", "SKIP", f"no router config at {path}")]
    try:
        doc = _router_loader.load(force=True)
    except Exception as e:
        return [CheckResult("router", "config", "FAIL", f"{path}: {e}")]
    agents = sorted((doc.get("agents") or {}).keys())
    default_client = doc.get("default_client") or "(unset)"
    return [
        CheckResult(
            "router",
            "config",
            "PASS",
            f"{path}: {len(agents)} agent(s) [{', '.join(agents)}], "
            f"default_client={default_client}",
        )
    ]


def check_deps() -> list[CheckResult]:
    parts = []
    for dist in _DEP_VERSIONS:
        try:
            parts.append(f"{dist} {importlib.metadata.version(dist)}")
        except importlib.metadata.PackageNotFoundError:
            continue
    return [CheckResult("deps", "versions", "PASS", ", ".join(parts) or "none")]


# ---------------------------------------------------------------------------
# --db group — opt-in; read-only (no writes, no file creation).
# ---------------------------------------------------------------------------


def db_checks() -> list[CheckResult]:
    if importlib.util.find_spec("sqlalchemy") is None:
        from pw_core._extras import install_target

        return [
            CheckResult(
                "db",
                "extra",
                "SKIP",
                f"sqlalchemy not installed — pip install {install_target('db')}",
            )
        ]

    from sqlalchemy import create_engine, text

    from pw_core.db import db_url
    from pw_core.db.connection import connect_args

    try:
        url = db_url()
    except Exception as e:
        return [CheckResult("db", "url", "FAIL", str(e))]
    results = [CheckResult("db", "url", "PASS", redact_value("DATABASE_URL", url))]

    # SQLite connects CREATE the file when absent — stat first so doctor
    # stays read-only.
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if db_path not in ("", ":memory:") and not Path(db_path).is_file():
            results.append(
                CheckResult("db", "connect", "FAIL", f"sqlite file not found: {db_path}")
            )
            results.append(CheckResult("db", "migrations", "SKIP", "no connection"))
            return results

    current_rev: str | None = None
    try:
        engine = create_engine(
            url, connect_args=connect_args(url, default_timeout_s=_PROBE_CONNECT_TIMEOUT_S)
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current_rev = _alembic_current(conn)
        engine.dispose()
        results.append(CheckResult("db", "connect", "PASS", "SELECT 1 ok"))
    except Exception as e:
        results.append(CheckResult("db", "connect", "FAIL", str(e)))
        results.append(CheckResult("db", "migrations", "SKIP", "no connection"))
        return results

    results.append(_migration_result(current_rev))
    return results


def _alembic_current(conn) -> str | None:
    from sqlalchemy import text

    try:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _migration_result(current_rev: str | None) -> CheckResult:
    alembic_dir = Path.cwd() / "alembic"
    if not (alembic_dir / "env.py").is_file():
        return CheckResult("db", "migrations", "SKIP", "no alembic/ in cwd")
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(alembic_dir))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as e:
        return CheckResult("db", "migrations", "WARN", f"cannot read script head: {e}")
    if current_rev is None:
        return CheckResult(
            "db",
            "migrations",
            "WARN",
            f"db has no alembic_version table; script head is {head}",
        )
    if current_rev != head:
        return CheckResult(
            "db",
            "migrations",
            "WARN",
            f"db at {current_rev}, script head {head} — run migrations",
        )
    return CheckResult("db", "migrations", "PASS", f"at head {head}")


def checks(*, db: bool = False, **_: object) -> list[CheckResult]:
    """Extras, env, router config and dependency versions; ``db`` adds the read-only database group."""
    out = [*check_extras(), *check_env(), *check_router(), *check_deps()]
    if db:
        out.extend(db_checks())
    return out


__all__ = ["checks"]
