"""pw-core's framework rules reach pw-foundation's gate when pw-core is installed."""

from __future__ import annotations

from pw_foundation.guards.framework_rules import (
    RULES as FOUNDATION_RULES,
    FrameworkRule,
    all_rules,
    layering_db_modules,
)

from pw_core import guards_rules


def test_rules_are_import_rules_for_the_http_libraries_pw_core_wraps():
    assert all(isinstance(r, FrameworkRule) and r.kind == "import" for r in guards_rules.RULES)
    assert {r.name for r in guards_rules.RULES} == {"requests", "httpx", "aiohttp", "urllib3"}
    assert all("pw_core.fetch" in r.use for r in guards_rules.RULES)


def test_names_do_not_collide_with_the_foundation_rules():
    assert not {r.name for r in guards_rules.RULES} & {r.name for r in FOUNDATION_RULES}


def test_the_gate_merges_them_in():
    merged = {r.name for r in all_rules()}
    assert {r.name for r in guards_rules.RULES} <= merged
    assert (
        layering_db_modules() == guards_rules.DB_MODULES == ("pw_core.db", "pw_core.db.connection")
    )
