"""Tests for the consumer test-bootstrap helpers in pw_core.testing.

Covers framework-table DDL generation, the pw_engine URL override and
teardown hook, resolver-cache clearing, the budget kill switch, the
CACHE_CONFIG "off" sentinel, and the hermetic-env / stub-router helpers.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import inspect, text

from pw_core.testing._server_db import active_dialect

# Module state for observing pw_engine teardown-hook execution (the hook
# fires after its test's body has finished, so a sibling test asserts).
_TEARDOWN_CALLS: list[str] = []

# MySQL has no CREATE INDEX IF NOT EXISTS, so re-running index DDL there errors.
_skip_on_mysql = pytest.mark.skipif(
    active_dialect() == "mysql", reason="MySQL index DDL is unconditional"
)


class TestMetadataDdl:
    """metadata_ddl compiles any MetaData into executable DDL strings."""

    @staticmethod
    def _sample_metadata():
        from sqlalchemy import Column, Index, Integer, MetaData, String, Table

        md = MetaData()
        Table(
            "md_items",
            md,
            Column("id", Integer, primary_key=True),
            Column("name", String(32)),
            Index("idx_md_items_name", "name"),
        )
        return md

    def test_emits_tables_and_indexes(self):
        from pw_core.testing.db_fixtures import metadata_ddl

        stmts = metadata_ddl(self._sample_metadata())
        joined = "\n".join(stmts)
        assert "md_items" in joined
        assert "CREATE INDEX" in joined and "idx_md_items_name" in joined

    @_skip_on_mysql
    def test_executes_and_is_idempotent(self, pw_engine):
        from pw_core.testing.db_fixtures import metadata_ddl

        stmts = metadata_ddl(self._sample_metadata())
        for _ in range(2):  # if_not_exists → safe to run twice
            with pw_engine.connect() as conn, conn.begin():
                for s in stmts:
                    conn.execute(text(s))
        assert "md_items" in inspect(pw_engine).get_table_names()


class TestFrameworkDdl:
    """framework_ddl emits DDL for every pw-core-owned table."""

    def test_creates_all_framework_tables(self, pw_engine):
        from pw_core.testing.db_fixtures import framework_ddl

        with pw_engine.connect() as conn, conn.begin():
            for s in framework_ddl():
                conn.execute(text(s))
        names = set(inspect(pw_engine).get_table_names())
        expected = {
            "jobs",
            "job_steps",
            "job_events",
            "llm_models",
            "llm_agent_types",
            "llm_prompts",
            "llm_runs",
            "llm_run_payloads",
            "llm_cache_entries",
            "llm_budgets",
            "llm_budget_snapshots",
            "llm_cost_rates",
        }
        assert expected <= names, f"missing: {expected - names}"
        assert "llm_embeddings" not in names

    def test_emits_no_embeddings_table(self):
        from pw_core.testing.db_fixtures import framework_ddl

        assert not any("llm_embeddings" in s for s in framework_ddl())

    @_skip_on_mysql
    def test_idempotent(self, pw_engine):
        from pw_core.testing.db_fixtures import framework_ddl

        for _ in range(2):
            with pw_engine.connect() as conn, conn.begin():
                for s in framework_ddl():
                    conn.execute(text(s))
        assert "llm_runs" in inspect(pw_engine).get_table_names()

    def test_postgresql_dialect_applies_variants(self):
        from pw_core.testing.db_fixtures import framework_ddl

        joined = "\n".join(framework_ddl(dialect="postgresql"))
        assert "JSONB" in joined

    def test_only_filters_to_named_tables(self):
        # A consumer that extends some framework tables in its own migrations
        # (extra columns on llm_runs, say) splices just the subset it shares.
        from pw_core.testing.db_fixtures import framework_ddl

        stmts = framework_ddl(only={"llm_models", "jobs", "job_steps", "job_events"})
        joined = "\n".join(stmts)
        assert sum(s.lstrip().startswith("CREATE TABLE") for s in stmts) == 4
        assert "llm_models" in joined
        assert "job_events" in joined
        assert "llm_runs" not in joined
        assert "llm_budgets" not in joined


class TestPfSchemaSplice:
    """framework_ddl() + project DDL composes a consumer pw_schema."""

    @pytest.fixture
    def pw_schema(self):
        from pw_core.testing.db_fixtures import framework_ddl

        return framework_ddl() + [
            """
            CREATE TABLE IF NOT EXISTS proj_refs (
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            )
            """
        ]

    def test_project_table_can_reference_framework_table(self, pw_tables, pw_connection):
        pw_connection.execute(text("INSERT INTO jobs (kind) VALUES ('probe')"))
        job_id = pw_connection.execute(text("SELECT id FROM jobs WHERE kind = 'probe'")).scalar()
        pw_connection.execute(text("INSERT INTO proj_refs (job_id) VALUES (:j)"), {"j": job_id})
        assert pw_connection.execute(text("SELECT COUNT(*) FROM proj_refs")).scalar() == 1


class TestEngineUrlOverride:
    """PW_TEST_DATABASE_URL redirects pw_engine to an operator-chosen backend."""

    @pytest.fixture
    def _alt_url(self, tmp_path_factory, monkeypatch):
        p = tmp_path_factory.mktemp("pw_alt") / "alt.sqlite"
        url = f"sqlite:///{p}"
        monkeypatch.setenv("PW_TEST_DATABASE_URL", url)
        return url

    def test_pf_engine_honors_override(self, _alt_url, pw_engine):
        assert str(pw_engine.url) == _alt_url
        with pw_engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1

    def test_database_url_env_matches_engine(self, _alt_url, pw_engine):
        assert os.environ["DATABASE_URL"] == _alt_url


class TestEngineTeardownHook:
    """pw_engine_teardown runs before engine.dispose(), engine still usable."""

    @pytest.fixture
    def pw_engine_teardown(self):
        def _hook():
            from pw_core.db import transaction

            # transaction() must still route to the (not yet disposed,
            # not yet unpatched) test engine when the hook fires.
            with transaction() as conn:
                _TEARDOWN_CALLS.append(f"alive={conn.execute(text('SELECT 1')).scalar()}")

        return _hook

    def test_hook_registered_engine_used(self, pw_engine):
        _TEARDOWN_CALLS.clear()
        with pw_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        # hook fires during this test's teardown; sibling test asserts

    def test_hook_fired_with_live_engine(self):
        assert _TEARDOWN_CALLS == ["alive=1"]


class TestResolverCacheCleared:
    """pw_engine clears tracking resolver caches — stale cross-test ids
    (ids cached against a disposed engine's DB) can't leak forward."""

    @pytest.fixture
    def pw_schema(self):
        from pw_core.testing.db_fixtures import framework_ddl

        return framework_ddl()

    def test_seed_resolver_cache(self, pw_tables):
        from pw_core.llm.tracking import resolve_agent_type_id

        assert isinstance(resolve_agent_type_id("bootstrap_probe"), int)

    def test_fresh_engine_resolves_against_fresh_db(self, pw_tables):
        from pw_core.db import transaction
        from pw_core.llm.tracking import resolve_agent_type_id

        # Without clearing, the id cached by the previous test would be
        # returned as-is and no row would exist in this fresh database.
        resolve_agent_type_id("bootstrap_probe")
        with transaction() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM llm_agent_types WHERE slug = 'bootstrap_probe'")
            ).scalar()
        assert n == 1


class TestBudgetKillSwitch:
    """BUDGET_ENFORCEMENT_DISABLED neutralizes the whole guard pair."""

    def test_project_cost_zero_without_db(self, monkeypatch):
        monkeypatch.setenv("BUDGET_ENFORCEMENT_DISABLED", "1")
        # No tables behind this URL: proves the switch short-circuits
        # before any DB access.
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        from pw_core.budget import project_cost

        assert project_cost(agent_type="probe", model="probe-model") == 0.0

    def test_pf_budget_disabled_fixture(self, pw_budget_disabled, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        from pw_core.budget import check_budget, project_cost

        assert os.environ["BUDGET_ENFORCEMENT_DISABLED"] == "1"
        assert project_cost(agent_type="probe", model="probe-model") == 0.0
        assert check_budget(agent_type="probe", projected_cost_usd=10.0) is None


class TestCacheConfigSentinel:
    """CACHE_CONFIG=off disables caching without a config file."""

    @pytest.mark.parametrize("sentinel", ["off", "OFF", "disabled", "none", "0"])
    def test_sentinel_disables_cache(self, monkeypatch, sentinel):
        from pw_core.llm.cache import config as cache_config

        monkeypatch.setenv("CACHE_CONFIG", sentinel)
        monkeypatch.setenv("CACHE_CONFIG_RELOAD_SECONDS", "0")
        cache_config.clear_config_cache()
        try:
            cfg = cache_config.get_agent_cache_config("any_agent")
            assert cfg.exact is False
        finally:
            cache_config.clear_config_cache()


class TestHermeticEnv:
    """hermetic_test_env() pins the standard no-external-services block."""

    _MANAGED = (
        "DATABASE_URL",
        "REDIS_URL",
        "CACHE_CONFIG",
        "CACHE_CONFIG_RELOAD_SECONDS",
        "BUDGET_ENFORCEMENT_DISABLED",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "BRAVE_API_KEY",
    )

    def test_pins_block_and_clears_keys(self, monkeypatch):
        # Simulate a polluted operator environment; monkeypatch restores it.
        for k in self._MANAGED:
            monkeypatch.setenv(k, "polluted")
        from pw_core.testing.env import hermetic_test_env

        hermetic_test_env()
        assert os.environ["DATABASE_URL"] == "sqlite://"
        assert os.environ["REDIS_URL"] == ""
        assert os.environ["CACHE_CONFIG"] == "off"
        assert os.environ["CACHE_CONFIG_RELOAD_SECONDS"] == "0"
        assert os.environ["BUDGET_ENFORCEMENT_DISABLED"] == "1"
        for k in (
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "BRAVE_API_KEY",
        ):
            assert k not in os.environ

    def test_overrides_and_extra(self, monkeypatch):
        for k in self._MANAGED:
            monkeypatch.setenv(k, "polluted")
        monkeypatch.setenv("MYAPP_MODE", "polluted")
        from pw_core.testing.env import hermetic_test_env

        hermetic_test_env(
            database_url="sqlite:///hermetic_probe.db",
            extra={"MYAPP_MODE": "1"},
        )
        assert os.environ["DATABASE_URL"] == "sqlite:///hermetic_probe.db"
        assert os.environ["MYAPP_MODE"] == "1"


class TestStubModelRouter:
    """stub_model_router writes a router YAML and points the env at it."""

    def test_agents_resolve_to_stub_model(self, tmp_path, monkeypatch):
        # Register restoration for the vars the helper sets.
        monkeypatch.setenv("MODEL_ROUTER_CONFIG", "sentinel")
        monkeypatch.setenv("MODEL_ROUTER_RELOAD_SECONDS", "sentinel")
        from pw_core.testing.env import stub_model_router

        path = stub_model_router(["alpha", "beta"], dir=tmp_path)
        assert os.environ["MODEL_ROUTER_CONFIG"] == str(path)
        assert os.environ["MODEL_ROUTER_RELOAD_SECONDS"] == "0"

        from pw_core.llm.router import get_agent_config

        assert get_agent_config("alpha")["model"] == "test-model"
        assert get_agent_config("beta")["model"] == "test-model"

    def test_custom_model_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MODEL_ROUTER_CONFIG", "sentinel")
        monkeypatch.setenv("MODEL_ROUTER_RELOAD_SECONDS", "sentinel")
        from pw_core.testing.env import stub_model_router

        stub_model_router(["gamma"], model="fake-fast", dir=tmp_path)
        from pw_core.llm.router import get_agent_config

        assert get_agent_config("gamma")["model"] == "fake-fast"
