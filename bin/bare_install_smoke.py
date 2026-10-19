"""Bare-install smoke check — run INSIDE a venv that has only `pip install .`
(no extras) of pw-core. Proves the foundation install is dependency-light and
that LLM/HTTP-gated modules raise a friendly ImportError naming their extra.

Invoked by `bin/verify-bare-install`. Not a pytest test (it must run in the
bare venv, not the dev venv) — the filename intentionally avoids `test_*` so
pytest does not collect it. Exits non-zero on any failure.
"""

import importlib
import sys

failures: list[str] = []


def check(desc, fn):
    try:
        fn()
        print(f"PASS: {desc}")
    except AssertionError as e:
        print(f"FAIL: {desc}: {e}")
        failures.append(desc)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {desc}: {type(e).__name__}: {e}")
        failures.append(desc)


# 1. Foundation modules import cleanly with only base deps installed.
foundation = [
    "pw_foundation",  # the hard dependency, with its five small deps
    "pw_core",
    "pw_core.config",
    "pw_core.doctor",
    "pw_core.guards_rules",
    "pw_core.utils",
    "pw_core.utils.similarity",
    "pw_core.utils.vocab",
    "pw_core.utils.relative_dates",
    # generic JSON-from-messy-text recovery (lives outside pw_core.llm).
    "pw_core.utils.json_recovery",
    # pure URL parsing / HTML metadata — no httpx.
    "pw_core.utils.url_parse",
    "pw_core.utils.url_html",
]
for mod in foundation:
    check(f"import {mod}", lambda m=mod: importlib.import_module(m))

# 2. Heavy deps must be absent in a bare install.
for pkg in ["httpx", "pydantic", "json_repair", "tenacity", "typer"]:

    def _absent(p=pkg):
        try:
            importlib.import_module(p)
        except ImportError:
            return
        raise AssertionError(f"{p} unexpectedly importable")

    check(f"{pkg} NOT installed", _absent)


# 3. Gated modules raise a friendly ImportError naming the extra + pip command.
def _friendly(mod, needle):
    try:
        importlib.import_module(mod)
    except ImportError as e:
        msg = str(e)
        assert "pip install pw-core[" in msg, f"no install hint in: {msg!r}"
        assert needle in msg, f"expected {needle!r} in: {msg!r}"
        return
    raise AssertionError(f"{mod} imported but should have raised")


check(
    "pw_core.llm.parse -> friendly [validate] error",
    lambda: _friendly("pw_core.llm.parse", "pw-core[validate]"),
)
check(
    "pw_core.llm.validate -> friendly [validate] error",
    lambda: _friendly("pw_core.llm.validate", "pw-core[validate]"),
)
check(
    "pw_core.clients.openrouter -> friendly [llm] error",
    lambda: _friendly("pw_core.clients.openrouter", "pw-core[llm]"),
)


# 4. Lazy util re-export raises a friendly [http] error on attribute access.
def _lazy_http():
    import pw_core.utils as u

    try:
        u.check_url  # noqa: B018
    except ImportError as e:
        assert "pw-core[http]" in str(e), str(e)
        return
    raise AssertionError("pw_core.utils.check_url did not raise")


check("pw_core.utils.check_url -> friendly [http] error", _lazy_http)


# 5. The stdlib-only helpers must resolve off the same facade with no extra —
# the negative check above passes just as well when everything is gated.
def _ungated_utils():
    import pw_core.utils as u

    assert u.domain_of("https://www.example.com/p") == "example.com"
    assert u.archive_timestamp_is_round("https://example.com") is False
    assert callable(u.canonical_url)
    assert callable(u.extract_path_date)
    assert callable(u.extract_article_metadata)


check("pw_core.utils pure helpers resolve without [http]", _ungated_utils)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL BARE-INSTALL CHECKS PASSED")
