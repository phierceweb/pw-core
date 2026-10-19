"""pw_core.testing — Shared test infrastructure for pw-core consumer projects.

This package provides two pytest plugins:

``pw_core.testing.fixtures`` (auto-loaded)
    Base plugin, registered via the ``pytest11`` entry point in pyproject.toml.
    Provides ``pw_app_client`` (httpx AsyncClient against a FastAPI app).
    No DB dependencies — safe for consumers without the ``[db]`` extra.

``pw_core.testing.db_fixtures`` (opt-in)
    DB plugin. Requires sqlalchemy (the ``[db]`` extra). Consumers who want
    DB fixtures must explicitly opt in by adding to their ``conftest.py``::

        pytest_plugins = ["pw_core.testing.db_fixtures"]

    Provides:
        pw_engine     — File-backed SQLite engine (per-test temp file), reset per test.
        pw_connection — Connection with an active transaction, rolled back after each test.
        pw_tables     — DDL from a ``pw_schema`` fixture and/or ``@pytest.mark.pw_tables(...)``.
"""
