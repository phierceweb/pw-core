# Testing

pw_core ships two pytest plugins:

- **`pw_core.testing.fixtures`** — auto-discovered via the `pytest11` entry point. Provides `pw_app_client` and `pw_budget_disabled`. No DB dependencies.
- **`pw_core.testing.db_fixtures`** — opt-in. Provides `pw_engine`, `pw_connection`, `pw_tables`. Requires the `[db]` extra (sqlalchemy).

To use the DB fixtures, add this to your `conftest.py`:

```python
pytest_plugins = ["pw_core.testing.db_fixtures"]
```

The split exists so that consumers who don't install `[db]` (e.g. a pipeline consumer using only pw-core's clients) don't pay an unconditional sqlalchemy import on every pytest run.

## Fixtures

### pw_engine

*(Requires `pytest_plugins = ["pw_core.testing.db_fixtures"]` in your conftest.py.)*


File-backed SQLite engine, fresh per test (its own temp file, `NullPool` + WAL). Each test is isolated, and because each connection is independent it is **safe under multi-threaded access** — tests that drive `pw_foundation.parallel.run_parallel` through the repos work without `SQLITE_MISUSE`. Patches `pw_core.db.connection` so that `get_engine()` and `transaction()` use the test engine.

```python
def test_something(pw_engine):
    # pw_core.db.transaction() now uses the test engine
    with transaction() as conn:
        conn.execute(text("SELECT 1"))
```

Setup also clears the `pw_core.llm.tracking` resolver caches (when the tracking closure is installed), so an id cached against a previous test's database can never leak into this one.

**Backend portability:** set `PW_TEST_DATABASE_URL` to point every test at a disposable Postgres/MySQL server instead of the per-test SQLite file — the same suite then exercises real-dialect SQL (`bin/test-pg`-style gates). Each test still gets a clean slate: `pw_engine` creates a `pw_test_<hex>` schema (Postgres, via `search_path`) or database (MySQL) at setup, points the engine and `DATABASE_URL` at it, and drops it at teardown. The URL's role needs `CREATE` on the database (Postgres) or `CREATE`/`DROP` on databases (MySQL). Server engines get `get_engine()`'s session setup (UTC time zone; MySQL foreign-key checks). Apart from tables an extension installed, a test reads only the tables it creates, through `pw_schema` / `pw_tables`. On Postgres the `search_path` is the test schema then `public`, so extension types and functions installed in `public` (pgvector's `vector`, `uuid_generate_v4()`) still resolve while unqualified `CREATE`s land in the test schema. A table in `public` would resolve too, so setup raises `ConfigurationError` naming each table or view there that no extension owns (pre-run migrations, say); point the URL at an empty database. On MySQL a test never sees the URL's own database. Teardown's `DROP` waits `PW_TEST_LOCK_TIMEOUT_S` seconds (default 10, `0` waits forever) for a lock a test left held, then fails that test's teardown rather than stalling the run. Never point it at real data.

Write DDL that runs on every backend you gate on: prefer `metadata_ddl()` over hand-written SQL, and in raw DDL avoid SQLite-only forms such as `AUTOINCREMENT`, `datetime('now')`, an `INTEGER PRIMARY KEY` inserted without an id, and `TEXT` primary or unique keys (MySQL needs a bounded `VARCHAR`). The raw-DDL examples on this page are SQLite-only. Read inserted ids with a Core `insert(table)` and `result.inserted_primary_key`, not `lastrowid`, which psycopg does not provide.

**Teardown hook:** override `pw_engine_teardown` to run something before `engine.dispose()` while the engine is still alive and patched in — e.g. draining background worker threads (disposing under a live worker can segfault SQLite):

```python
# conftest.py
@pytest.fixture
def pw_engine_teardown():
    from app.jobs import wait_all

    return lambda: wait_all(timeout=10.0)
```

### Framework tables — `framework_ddl()` / `metadata_ddl()`

Tests that exercise pw-core's jobs / tracking / cache / budget subsystems need the framework tables in the test database. **Never hand-copy the CREATE TABLE statements** — they drift when pw-core's schema changes. Splice the generated DDL into `pw_schema`:

```python
# conftest.py
from pw_core.testing.db_fixtures import framework_ddl


@pytest.fixture
def pw_schema():
    return framework_ddl() + PROJECT_DDL  # framework first: project FKs may reference jobs(id)
```

`framework_ddl(dialect=None, if_not_exists=True)` emits every pw-core-owned table (tracking, jobs, cache, budget) in dependency order, then indexes. `dialect=None` compiles for the backend `pw_engine` targets — `PW_TEST_DATABASE_URL`'s, else SQLite — so the same `pw_schema` runs on every gate. MySQL has no `CREATE INDEX IF NOT EXISTS`, so its index statements are unconditional and re-running the DDL against the same database fails there. `metadata_ddl(metadata, ...)` does the same for any SQLAlchemy `MetaData` — use it for a project's own declarative metadata, or with `pw_core.jobs._schema.metadata` in a jobs-only consumer that doesn't install the `[tracking]` closure.

Both take `only={"table", ...}` to restrict output to named tables. Use it when your project's migrations **extend** some framework tables (extra columns) — splice the subset you share verbatim and let your own fixtures create the extended shapes (define the extensions with [`pw_core.db.types`](database.md) so the column types match the framework's on every dialect):

```python
return framework_ddl(only={"llm_models", "jobs", "job_steps", "job_events"}) + PROJECT_DDL
```

### pw_budget_disabled

Neutralizes the cost-budget guard for a test: `check_budget()` no-ops and `project_cost()` returns `0.0` without touching the DB (sets `BUDGET_ENFORCEMENT_DISABLED=1`, read per call — works for every import style). For suites where services are tested with mocked repos, autouse-wrap it:

```python
# conftest.py
@pytest.fixture(autouse=True)
def _no_budget(pw_budget_disabled):
    yield
```

### pw_connection

A `Connection` inside a SAVEPOINT, rolled back after each test. Tests never see each other's data.

```python
def test_insert(pw_tables, pw_connection):
    pw_connection.execute(
        text("INSERT INTO items (name) VALUES (:name)"),
        {"name": "test"},
    )
    row = pw_connection.execute(text("SELECT * FROM items")).fetchone()
    assert row is not None
```

### pw_tables

Creates tables before the test runs. Two ways to provide DDL:

**Via marker** (per-test):

```python
@pytest.mark.pw_tables("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
def test_widgets(pw_tables, pw_connection): ...
```

**Via fixture** (project-wide):

```python
# conftest.py
@pytest.fixture
def pw_schema():
    return [
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)",
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE)",
    ]


def test_items(pw_tables, pw_connection): ...
```

A `pw_schema` fixture applies to every test that uses `pw_tables`, whether or not the test names `pw_schema` in its arguments, and whether or not it is `autouse`.

Both can be combined — marker DDL runs after `pw_schema` and **supplements** it. Marker DDL that re-creates a table, index, or view `pw_schema` already creates raises `InvalidInputError` naming the object. To give one module a different shape for a shared table, override `pw_schema` in that module instead:

```python
# test_widgets.py — module-local schema replaces the conftest one
@pytest.fixture
def pw_schema():
    return ["CREATE TABLE items (id INTEGER PRIMARY KEY, other TEXT)"]
```

### pw_app_client

httpx `AsyncClient` bound to your FastAPI app. Requires you to define a `pw_app` fixture:

```python
# conftest.py
@pytest.fixture
def pw_app():
    from app.api import app

    return app


# test_api.py
async def test_health(pw_app_client):
    resp = await pw_app_client.get("/api/health")
    assert resp.status_code == 200
```

## Example: full test setup

```python
# conftest.py
import pytest
from pw_core.db import transaction
from sqlalchemy import text


@pytest.fixture
def pw_schema():
    return [
        "CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
        "CREATE TABLE items (id TEXT PRIMARY KEY, category_id INTEGER REFERENCES categories(id))",
    ]


@pytest.fixture
def pw_app():
    from app import create_app

    return create_app()


# test_items.py
def test_create_item(pw_tables, pw_connection):
    pw_connection.execute(text("INSERT INTO categories (id, name) VALUES (1, 'Test')"))
    pw_connection.execute(text("INSERT INTO items (id, category_id) VALUES ('I001', 1)"))

    row = pw_connection.execute(text("SELECT * FROM items WHERE id = 'I001'")).fetchone()
    assert row is not None


async def test_api_categories(pw_tables, pw_app_client):
    resp = await pw_app_client.get("/api/categories")
    assert resp.status_code == 200
```

## Hermetic env — `pw_core.testing.env`

Consumer config objects read the environment at **import time**, so pytest fixtures run too late for the base env block. Call these at the top of `conftest.py`, before any app import:

```python
# conftest.py — first lines
from pw_core.testing.env import hermetic_test_env, stub_model_router

hermetic_test_env(extra={"MYAPP_MODE": "1"})
stub_model_router(["summarizer", "classifier"])
```

- `hermetic_test_env()` forces `DATABASE_URL` (default `sqlite://`), blanks `REDIS_URL`, sets `CACHE_CONFIG=off` (+ reload `0`) and `BUDGET_ENFORCEMENT_DISABLED=1`, and deletes provider API keys (`OPENROUTER`/`ANTHROPIC`/`OPENAI`/`BRAVE`) so a misconfigured test can't reach a real provider. Keyword args opt individual pieces out; `extra={...}` sets project-specific vars.
- `stub_model_router(agents, model="test-model", dir=None)` writes a stub router YAML mapping each slug to *model* and points `MODEL_ROUTER_CONFIG` at it (reload TTL `0`) — replaces per-project `model_router_test.yaml` fixture copies and keeps `assert_agents_registered` happy.

## Reset helpers

For integration tests or fixtures that need a clean singleton:

```python
from pw_core.db.connection import reset_engine
from pw_core.db.models import clear_cache
from pw_core.clients.openrouter import reset_client
from pw_core.cache.redis import reset_cache

reset_engine()  # dispose and reset the DB engine singleton
clear_cache()  # clear the model name → ID cache
reset_client()  # reset the OpenRouter client singleton
reset_cache()  # reset the RedisCache singleton
```
