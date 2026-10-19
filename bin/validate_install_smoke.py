"""[validate]-install smoke check — run INSIDE a venv that has only
`pip install .[validate]` of pw-core. Proves the anti-slop output guards
(parse + json-repair, pydantic validation) are usable WITHOUT the client/HTTP
stack, and that client- and tracking-tier members still guard cleanly.

Invoked by `bin/verify-bare-install`. Not collected by pytest (no `test_`
prefix). Exits non-zero on any failure.
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


# 1. Anti-slop guards import with [validate] only (json-repair + pydantic).
for mod in ["pw_core.llm.parse", "pw_core.llm.validate"]:
    check(f"import {mod} (under [validate])", lambda m=mod: importlib.import_module(m))

# 2. The client/HTTP stack is NOT present — [validate] is guards-only.
for pkg in ["httpx", "tenacity"]:

    def _absent(p=pkg):
        try:
            importlib.import_module(p)
        except ImportError:
            return
        raise AssertionError(f"{p} unexpectedly importable under [validate]")

    check(f"{pkg} NOT installed under [validate]", _absent)


def _friendly(mod, needle):
    try:
        importlib.import_module(mod)
    except ImportError as e:
        msg = str(e)
        assert "pip install pw-core[" in msg, f"no install hint in: {msg!r}"
        assert needle in msg, f"expected {needle!r} in: {msg!r}"
        return
    raise AssertionError(f"{mod} imported but should have raised")


# 3. Clients still need [llm]; tracked still needs [tracking] (DB recording).
check(
    "pw_core.clients.openrouter -> friendly [llm] error",
    lambda: _friendly("pw_core.clients.openrouter", "pw-core[llm]"),
)
check(
    "pw_core.llm.tracked -> friendly [tracking] error",
    lambda: _friendly("pw_core.llm.tracked", "pw-core[tracking]"),
)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL [validate]-INSTALL CHECKS PASSED")
