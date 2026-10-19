"""Tests for the pw_core.testing plugin itself.

Verifies that the fixtures work correctly: engine creation, connection
isolation, table creation, and transaction rollback between tests.
"""

import pytest
from sqlalchemy import text

from pw_core.testing._ddl import created_objects, dropped_objects
from pw_core.testing._server_db import active_dialect


class TestEngineFixture:
    """pw_engine provides a working engine on the configured backend."""

    def test_engine_matches_configured_backend(self, pw_engine):
        assert pw_engine.dialect.name == active_dialect()

    def test_engine_can_execute(self, pw_engine):
        with pw_engine.connect() as conn:
            result = conn.execute(text("SELECT 1 AS ok"))
            assert result.scalar() == 1


class TestConnectionFixture:
    """pw_connection provides an isolated, rolled-back connection."""

    def test_insert_and_query(self, pw_tables, pw_connection):
        pw_connection.execute(
            text("INSERT INTO items (name) VALUES (:name)"),
            {"name": "test_item"},
        )
        row = pw_connection.execute(
            text("SELECT name FROM items WHERE name = :name"),
            {"name": "test_item"},
        ).fetchone()
        assert row[0] == "test_item"

    def test_previous_insert_not_visible(self, pw_tables, pw_connection):
        """Data from test_insert_and_query should not leak here."""
        row = pw_connection.execute(
            text("SELECT COUNT(*) FROM items"),
        ).fetchone()
        assert row[0] == 0


class TestConcurrentAccess:
    """pw_engine must be safe under multi-threaded repo access: each worker thread
    gets its own connection."""

    @pytest.mark.pw_tables("CREATE TABLE concurrent_items (val INTEGER NOT NULL)")
    def test_parallel_writes_through_transaction(self, pw_tables):
        from pw_core.db import transaction
        from pw_foundation.parallel import run_parallel

        def _insert(n: int) -> None:
            with transaction() as conn:
                conn.execute(
                    text("INSERT INTO concurrent_items (val) VALUES (:v)"),
                    {"v": n},
                )

        run_parallel(items=list(range(50)), fn=_insert, workers=4, label="Inserted")

        with transaction() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM concurrent_items")).scalar()
        assert count == 50

    @pytest.mark.pw_tables("CREATE TABLE repo_concurrent (val INTEGER NOT NULL)")
    def test_parallel_writes_through_repository(self, pw_tables):
        """The layering-mandated path: services reach the DB via a
        ``Repository``, not raw ``transaction()``. A repo opening its own
        transaction per call from many threads must be just as safe."""
        from pw_core.db import Repository, transaction
        from pw_foundation.parallel import run_parallel

        class _CounterRepo(Repository):
            def add(self, n: int) -> None:
                with self._tx() as conn:
                    conn.execute(
                        text("INSERT INTO repo_concurrent (val) VALUES (:v)"),
                        {"v": n},
                    )

        run_parallel(
            items=list(range(50)),
            fn=lambda n: _CounterRepo().add(n),
            workers=4,
            label="Repo-inserted",
        )

        with transaction() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM repo_concurrent")).scalar()
        assert count == 50

    @pytest.mark.pw_tables("CREATE TABLE mixed_rw (val INTEGER NOT NULL)")
    def test_concurrent_reads_during_writes(self, pw_tables):
        """Readers must not error while writers are committing."""
        from pw_core.db import transaction
        from pw_foundation.parallel import run_parallel

        def _write_then_read(n: int) -> None:
            with transaction() as conn:
                conn.execute(text("INSERT INTO mixed_rw (val) VALUES (:v)"), {"v": n})
            # Read while other workers are mid-write — must not raise.
            with transaction() as conn:
                conn.execute(text("SELECT COUNT(*) FROM mixed_rw")).scalar()

        run_parallel(items=list(range(50)), fn=_write_then_read, workers=4, label="RW")

        with transaction() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM mixed_rw")).scalar()
        assert count == 50


class TestFixtureThreadSafetyInvariant:
    """pw_engine must stay NullPool and file-backed: a shared in-memory ``StaticPool``
    connection raises SQLITE_MISUSE before any concurrency test can run."""

    def test_pf_engine_uses_nullpool(self, pw_engine):
        from sqlalchemy.pool import NullPool

        assert isinstance(pw_engine.pool, NullPool), (
            f"pw_engine must use NullPool for thread-safe concurrent access; "
            f"got {type(pw_engine.pool).__name__}"
        )

    def test_pf_engine_is_file_backed_not_in_memory(self, pw_engine):
        db = pw_engine.url.database
        assert db not in (None, "", ":memory:"), (
            f"pw_engine must be file-backed (per-worker connections); "
            f"got in-memory url {pw_engine.url!r}"
        )


class TestTablesMarker:
    """@pytest.mark.pw_tables creates tables from inline DDL."""

    @pytest.mark.pw_tables("CREATE TABLE custom_table (val TEXT)")
    def test_marker_creates_table(self, pw_tables, pw_connection):
        pw_connection.execute(
            text("INSERT INTO custom_table (val) VALUES (:v)"),
            {"v": "hello"},
        )
        row = pw_connection.execute(text("SELECT val FROM custom_table")).fetchone()
        assert row[0] == "hello"


class TestSchemaFixture:
    """pw_schema fixture (from conftest.py) creates project tables."""

    def test_items_table_exists(self, pw_tables, pw_connection):
        pw_connection.execute(
            text("INSERT INTO items (name) VALUES (:name)"),
            {"name": "from_schema"},
        )
        row = pw_connection.execute(
            text("SELECT name FROM items WHERE name = :name"),
            {"name": "from_schema"},
        ).fetchone()
        assert row[0] == "from_schema"

    def test_usage_events_table_exists(self, pw_tables, pw_connection):
        pw_connection.execute(
            text(
                "INSERT INTO usage_events (prompt_tokens, completion_tokens, cost_usd) "
                "VALUES (:pt, :ct, :cost)"
            ),
            {"pt": 100, "ct": 50, "cost": 0.002},
        )
        row = pw_connection.execute(text("SELECT cost_usd FROM usage_events")).fetchone()
        assert row[0] == pytest.approx(0.002)


_SCHEMA_CONFTEST = """
    import pytest

    pytest_plugins = ["pw_core.testing.db_fixtures"]

    @pytest.fixture
    def pw_schema():
        return ["CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"]
"""

_BARE_CONFTEST = """
    pytest_plugins = ["pw_core.testing.db_fixtures"]
"""


class TestSchemaFixtureWithoutAutouse:
    """A plain (non-autouse) ``pw_schema`` applies whether or not a test names it.

    pw-core's own conftest marks ``pw_schema`` autouse, which hides this path —
    an inner pytest session is the only way to observe it.
    """

    @pytest.fixture(autouse=True)
    def _sqlite_inner_session(self, monkeypatch):
        # The inner DDL is SQLite; these tests cover DDL merging, not dialects.
        monkeypatch.delenv("PW_TEST_DATABASE_URL", raising=False)

    def test_schema_alone_creates_tables(self, pytester):
        pytester.makeconftest(_SCHEMA_CONFTEST)
        pytester.makepyfile(
            """
            from sqlalchemy import text

            def test_items_exists(pw_tables, pw_connection):
                pw_connection.execute(text("INSERT INTO items (name) VALUES ('x')"))
                assert pw_connection.execute(text("SELECT COUNT(*) FROM items")).scalar() == 1
            """
        )
        pytester.runpytest_subprocess().assert_outcomes(passed=1)

    def test_marker_alone_creates_tables(self, pytester):
        pytester.makeconftest(_BARE_CONFTEST)
        pytester.makepyfile(
            """
            import pytest
            from sqlalchemy import text

            @pytest.mark.pw_tables("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
            def test_widgets_exists(pw_tables, pw_connection):
                pw_connection.execute(text("INSERT INTO widgets (id) VALUES (1)"))
                assert pw_connection.execute(text("SELECT COUNT(*) FROM widgets")).scalar() == 1
            """
        )
        pytester.runpytest_subprocess().assert_outcomes(passed=1)

    def test_schema_and_marker_both_apply(self, pytester):
        pytester.makeconftest(_SCHEMA_CONFTEST)
        pytester.makepyfile(
            """
            import pytest
            from sqlalchemy import text

            @pytest.mark.pw_tables("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
            def test_both_tables_exist(pw_tables, pw_connection):
                pw_connection.execute(text("INSERT INTO items (name) VALUES ('x')"))
                pw_connection.execute(text("INSERT INTO widgets (id) VALUES (1)"))
                assert pw_connection.execute(text("SELECT COUNT(*) FROM items")).scalar() == 1
                assert pw_connection.execute(text("SELECT COUNT(*) FROM widgets")).scalar() == 1
            """
        )
        pytester.runpytest_subprocess().assert_outcomes(passed=1)

    def test_marker_ddl_runs_after_schema(self, pytester):
        pytester.makeconftest(_SCHEMA_CONFTEST)
        pytester.makepyfile(
            """
            import pytest
            from sqlalchemy import text

            @pytest.mark.pw_tables("CREATE INDEX items_name_idx ON items (name)")
            def test_index_on_schema_table(pw_tables, pw_connection):
                row = pw_connection.execute(
                    text("SELECT name FROM sqlite_master WHERE name = 'items_name_idx'")
                ).fetchone()
                assert row is not None
            """
        )
        pytester.runpytest_subprocess().assert_outcomes(passed=1)

    def test_marker_redefining_schema_object_is_a_clear_error(self, pytester):
        pytester.makeconftest(_SCHEMA_CONFTEST)
        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.pw_tables("CREATE TABLE items (id INTEGER PRIMARY KEY, other TEXT)")
            def test_collision(pw_tables):
                pass
            """
        )
        result = pytester.runpytest_subprocess()
        result.assert_outcomes(errors=1)
        result.stdout.fnmatch_lines(["*redefines table items*pw_schema*"])

    def test_marker_if_not_exists_collision_is_not_silently_dropped(self, pytester):
        pytester.makeconftest(_SCHEMA_CONFTEST)
        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.pw_tables(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, other TEXT)"
            )
            def test_collision(pw_tables):
                pass
            """
        )
        result = pytester.runpytest_subprocess()
        result.assert_outcomes(errors=1)
        result.stdout.fnmatch_lines(["*redefines table items*"])

    def test_marker_may_drop_and_recreate_a_pf_schema_table(self, pytester):
        """An explicit DROP means the marker is replacing the table, not colliding."""
        pytester.makeconftest(_SCHEMA_CONFTEST)
        pytester.makepyfile(
            """
            import pytest
            from sqlalchemy import text

            @pytest.mark.pw_tables(
                "DROP TABLE items",
                "CREATE TABLE items (id INTEGER PRIMARY KEY, other TEXT)",
            )
            def test_replaced(pw_tables):
                with pw_tables.connect() as conn:
                    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(items)"))}
                assert cols == {"id", "other"}
            """
        )
        result = pytester.runpytest_subprocess()
        result.assert_outcomes(passed=1)


class TestCreatedObjects:
    """The collision detector recognises the DDL forms pw_schema DDL takes."""

    @pytest.mark.parametrize(
        "stmt,expected",
        [
            ("CREATE TABLE items (id INTEGER)", ("table", "items")),
            ("create table if not exists Items (id INTEGER)", ("table", "items")),
            ('CREATE TABLE "items" (id INTEGER)', ("table", "items")),
            ("CREATE UNIQUE INDEX items_ix ON items (name)", ("index", "items_ix")),
            ("CREATE TEMPORARY VIEW v AS SELECT 1", ("view", "v")),
            ("CREATE OR REPLACE VIEW v AS SELECT 1", ("view", "v")),
        ],
    )
    def test_recognises_create_forms(self, stmt, expected):
        assert created_objects([stmt]) == {expected}

    def test_ignores_non_create_statements(self):
        assert created_objects(["INSERT INTO items (id) VALUES (1)"]) == set()


class TestDroppedObjects:
    """Marker DDL that drops an object before recreating it is replacing, not colliding."""

    @pytest.mark.parametrize(
        "stmt,expected",
        [
            ("DROP TABLE items", ("table", "items")),
            ("drop table if exists Items", ("table", "items")),
            ('DROP TABLE "items"', ("table", "items")),
            ("DROP INDEX items_ix", ("index", "items_ix")),
            ("DROP VIEW v", ("view", "v")),
        ],
    )
    def test_recognises_drop_forms(self, stmt, expected):
        assert dropped_objects([stmt]) == {expected}

    def test_ignores_non_drop_statements(self):
        assert dropped_objects(["CREATE TABLE items (id INTEGER)"]) == set()
