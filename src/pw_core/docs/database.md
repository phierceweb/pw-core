# Database

SQLAlchemy-based database layer supporting SQLite, MySQL/MariaDB, and PostgreSQL. Provides engine management, transactions, helper functions, a model name resolver, and a repository base class.

## Installation

Database support is included in the base `pw-core` install (SQLAlchemy). For MySQL/MariaDB add `pw-core[mysql]` (PyMySQL); bare `mysql://` (and `mariadb://`) URLs resolve to PyMySQL at engine creation, so an explicit `mysql+pymysql://` driver suffix is optional. For PostgreSQL add `pw-core[postgres]` (psycopg 3 with the binary wheel). SQLite needs no extra.

## Engine setup

The engine initializes from `DATABASE_URL` env var. Call `get_engine()` at startup to validate the connection:

```python
from pw_core.db import get_engine, db_url

# Reads DATABASE_URL from env (works for any supported dialect)
get_engine()

# PostgreSQL example URL:
#   DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname

# SQLite with fallback (if DATABASE_URL is not set, uses SQLite)
get_engine(db_url(fallback_sqlite="app.db"))
```

The engine is cached as a module-level singleton — subsequent calls return the same engine, and concurrent first callers are serialized so exactly one engine (and one pool) is ever built. A later call passing a different URL still returns the cached engine and logs a `get_engine_url_ignored` warning (URLs credential-redacted); call `pw_core.db.connection.reset_engine()` first to switch databases.

### URL resolution

`db_url()` resolves the database URL in this order:

1. `DATABASE_URL` environment variable
2. `fallback_sqlite` path (becomes `sqlite:///path`)
3. Raises `DatabaseUnavailableError`

### Dialect detection

For code that needs to branch on the active dialect (Alembic env, raw-SQL helpers, etc.):

```python
from pw_core.db import is_sqlite, is_postgres, dialect_of

is_sqlite()  # True / False
is_postgres()  # True / False
dialect_of()  # "sqlite" | "mysql" | "postgresql"
```

`dialect_of()` returns the same string the [`json_compat`](../db/json_compat.py) helpers expect, so the two compose:

```python
from pw_core.db import dialect_of
from pw_core.db.json_compat import json_col_type

ddl = f"payload {json_col_type(dialect_of())} NOT NULL"
```

Each accepts an optional explicit URL (`is_postgres("postgresql+psycopg://...")`); without one they resolve from `DATABASE_URL`. `dialect_of()` raises `ConfigurationError` for an unsupported dialect.

### SQLite pragmas

When using SQLite, the engine automatically enables:
- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`
- `PRAGMA busy_timeout = <SQLITE_BUSY_TIMEOUT>` — how long (ms) concurrent writers wait for a lock before failing. Defaults to `30000` (30s). Set the `SQLITE_BUSY_TIMEOUT` env var to override (e.g. `0` to fail immediately).

### Connect timeout

MySQL and PostgreSQL engines are created with `connect_timeout` so an unreachable host fails fast instead of blocking the caller until the OS gives up on the socket. Precedence:

1. A `connect_timeout` in the `DATABASE_URL` query string wins — pw-core injects nothing, because a `connect_args` entry would silently override the URL.
2. `DB_CONNECT_TIMEOUT_S` (seconds).
3. Default `10`.

Setting `DB_CONNECT_TIMEOUT_S=0` injects nothing and leaves **the driver's own default** in force — unbounded for libpq (psycopg), but pymysql applies its own 10s connect timeout. It does not mean "unbounded" everywhere.

SQLite (no socket) is unaffected, as are drivers that don't take a `connect_timeout` kwarg — anything other than pymysql, MySQLdb, psycopg, psycopg2.

Only the TCP/handshake phase is bounded. Statement execution and pool checkout have their own (unbounded / `pool_timeout`) limits.

`pw-doctor --db` resolves its probe timeout through the same helper (`pw_core.db.connection.connect_args`), with a `5` second default instead of `10`.

### MySQL foreign keys

When using MySQL, the engine enables `SET foreign_key_checks = 1` on each connection.

### PostgreSQL

Each connection runs `SET TIME ZONE 'UTC'`, so `TIMESTAMPTZ` values come back in UTC whatever the server's `timezone` setting; FK constraints are always enforced. JSON columns use `JSONB` (preferred over `JSON`) — see `json_compat.json_col_type("postgresql")`.

## Transactions

`transaction()` is the primary way to run queries. It yields a `Connection` inside a transaction that commits on clean exit and rolls back on exception:

```python
from sqlalchemy import text
from pw_core.db import transaction

# Read
with transaction() as conn:
    rows = (
        conn.execute(
            text("SELECT * FROM items WHERE category_id = :cid"),
            {"cid": 3},
        )
        .mappings()
        .fetchall()
    )
    items = [dict(r) for r in rows]

# Write (auto-commits on exit)
with transaction() as conn:
    conn.execute(
        text("INSERT INTO items (id, title) VALUES (:id, :title)"),
        {"id": "ITEM_001", "title": "New item"},
    )

# Multiple writes in one transaction
with transaction() as conn:
    conn.execute(text("DELETE FROM item_tags WHERE item_id = :iid"), {"iid": "ITEM_001"})
    conn.execute(
        text("INSERT INTO item_tags (item_id, tag_id) VALUES (:iid, :tid)"),
        {"iid": "ITEM_001", "tid": 3},
    )
```

### Key patterns

**Named parameters**: Always use `:name` style, never `%s`:

```python
conn.execute(text("SELECT * FROM t WHERE id = :id"), {"id": 42})
```

**Dict-like rows**: Use `.mappings()` to get dict-like results:

```python
row = conn.execute(text("SELECT * FROM t WHERE id = :id"), {"id": 42}).mappings().fetchone()
if row:
    return dict(row)  # {"id": 42, "name": "..."}
```

**Dynamic IN clauses**: Build named parameter placeholders:

```python
params = {f"id_{i}": v for i, v in enumerate(ids)}
placeholders = ", ".join(f":id_{i}" for i in range(len(ids)))
rows = (
    conn.execute(
        text(f"SELECT * FROM items WHERE id IN ({placeholders})"),
        params,
    )
    .mappings()
    .fetchall()
)
```

**Dynamic SET clauses** (for partial updates):

```python
sets = ", ".join(f"{col} = :{col}" for col in fields)
params = dict(fields)
params["_id"] = record_id
conn.execute(text(f"UPDATE mytable SET {sets} WHERE id = :_id"), params)
```

**Last inserted ID** — use a Core `insert()` and `inserted_primary_key`, which SQLAlchemy fills on every dialect. `result.lastrowid` works on SQLite and MySQL only; psycopg has no `lastrowid`, so it raises on Postgres:

```python
from sqlalchemy import Column, Integer, MetaData, String, Table, insert

t = Table("t", MetaData(), Column("id", Integer, primary_key=True), Column("name", String(255)))

result = conn.execute(insert(t).values(name="x"))
new_id = result.inserted_primary_key[0]
```

## Migrating from pymysql

If you're converting code that used raw pymysql cursors, watch for these:

| pymysql | SQLAlchemy `text()` |
|---------|-------------------|
| `%s` positional params | `:name` named params |
| `.fetchone()` returns dict | `.mappings().fetchone()` for dict-like rows |
| `cur.lastrowid` | `result.inserted_primary_key[0]` from a Core `insert()` (see above) |
| `cur.rowcount` | `result.rowcount` |
| `%%` to escape `%` in SQL strings | Single `%` — no escaping needed |
| `WHERE id IN %s` with tuple | Build `:p0, :p1, ...` placeholders (see dynamic IN above) |

**The `%%` gotcha**: pymysql uses `%s` for parameters, so literal `%` in SQL (like MySQL's `DATE_FORMAT(col, '%Y-%m')`) must be doubled: `%%Y-%%m`. With SQLAlchemy `text()`, parameters use `:name`, so `%` is just a literal character. If you forget to un-double them, MySQL receives `%%Y-%%m` and returns the literal string `%Y-%m` instead of a formatted date. This is silent — no error, just wrong data.

## Helpers

```python
from pw_core.db import coerce_json_col, dumps_json, now_iso, row_to_dict
```

### Writing portable hand-written SQL

A query written against one dialect is a query the test fixture cannot run.
`pw_core.db.dialect` supplies the fragments that differ — `now_sql(conn)`,
`insert_ignore_prefix(conn)`, `row_lock_suffix(conn)` — plus `utc_cutoff()` for
threshold comparisons, all taking the `conn` you already hold. It also documents
when a cutoff belongs on the server instead (leases and retention deletes, where
clock skew between hosts can change the outcome). See
**[db-dialect.md](db-dialect.md)**.

### coerce_json_col

Safely coerce a database column value to a Python list. Handles `None`, JSON strings, lists, and other iterables. Never raises.

```python
coerce_json_col(None)  # []
coerce_json_col('["a", "b"]')  # ["a", "b"]
coerce_json_col([1, 2, 3])  # [1, 2, 3]
coerce_json_col("")  # []
```

### dumps_json

Serialize to JSON without ASCII-escaping unicode:

```python
dumps_json({"name": "Café Müller"})  # '{"name": "Café Müller"}'
```

### now_iso

Current UTC time as ISO 8601 string. Canonical home is `pw_foundation.utils.dates`; re-exported here for backward compatibility.

```python
from pw_core.db import now_iso  # works (backward compat)
from pw_foundation.utils.dates import now_iso  # preferred

now_iso()  # "2026-04-14T14:30:00Z"
```

See [dates.md](https://github.com/phierceweb/pw-foundation/blob/main/src/pw_foundation/docs/dates.md) for additional date utilities (`parse_date`, `month_label`, `date_range`, etc.).

### row_to_dict

Convert a SQLAlchemy Row, RowMapping, or dict to a plain dict:

```python
row = conn.execute(text("SELECT ...")).fetchone()
item = row_to_dict(row)  # plain dict or None
```

## Column type variants — `pw_core.db.types`

Cross-dialect column types the framework's own tables are built from. Use them when defining tables adjacent to (or extending) the framework schema so DDL matches on MySQL, PostgreSQL, and SQLite:

```python
from pw_core.db.types import FK_INT, PK_INT, TIMESTAMP_US, server_now

my_refs = Table(
    "my_refs",
    metadata,
    Column("id", PK_INT, primary_key=True, autoincrement=True),
    Column("job_id", FK_INT, ForeignKey("jobs.id", ondelete="CASCADE")),
    Column("created_at", TIMESTAMP_US, nullable=False, server_default=server_now()),
)
```

Available: `PK_INT` / `PK_SMALL` / `PK_BIG`, `FK_INT` / `FK_SMALL` / `FK_BIG` (UNSIGNED on MySQL; `Integer` base so SQLite autoincrement works), `TIMESTAMP_US` (microsecond precision; TIMESTAMPTZ on Postgres), `LARGE_TEXT` (MEDIUMTEXT on MySQL), `JSON_` (JSONB on Postgres; Python `None` stores as SQL NULL, not JSON `null` — pass `sqlalchemy.JSON.NULL` to store a JSON null), and `server_now()` (`CURRENT_TIMESTAMP(6)` on MySQL — required for `TIMESTAMP(6)` defaults under STRICT_TRANS_TABLES).

## Model name resolver

Thread-safe, cached resolver that maps LLM model names to database IDs. Uses a `models` table with `id` (auto-increment) and `name` (unique) columns.

```python
from pw_core.db import resolve_model_id

model_id = resolve_model_id("anthropic/claude-sonnet-4.6")  # int or None
```

- Thread-safe with a process-level lock and cache
- Insert-or-ignore + SELECT via the dialect-agnostic [`insert_ignore`](db-upsert.md) helper — `ON CONFLICT (name) DO NOTHING` on SQLite/PostgreSQL, a no-op `ON DUPLICATE KEY UPDATE` on MySQL/MariaDB; never the Postgres-invalid `INSERT IGNORE`
- Each model name is inserted at most once per process lifetime
- Returns `None` for empty strings

## Repository base class

For organizing query functions into classes with shared transaction management:

```python
from pw_core.db.repository import Repository
from sqlalchemy import text


class ItemRepo(Repository):
    def get_by_id(self, item_id: str) -> dict | None:
        with self._tx() as conn:
            row = (
                conn.execute(
                    text("SELECT * FROM items WHERE id = :id"),
                    {"id": item_id},
                )
                .mappings()
                .fetchone()
            )
            return dict(row) if row else None

    def list_by_category(self, category_id: int) -> list[dict]:
        with self._tx() as conn:
            rows = (
                conn.execute(
                    text("SELECT * FROM items WHERE category_id = :cid ORDER BY created_at"),
                    {"cid": category_id},
                )
                .mappings()
                .fetchall()
            )
            return [dict(r) for r in rows]
```

### Standalone vs shared transaction

```python
# Standalone — each method creates its own transaction
repo = ItemRepo()
item = repo.get_by_id("ITEM_001")

# Shared — multiple operations in one transaction
with transaction() as conn:
    repo = ItemRepo(conn)
    item = repo.get_by_id("ITEM_001")
    in_category = repo.list_by_category(3)
    # Both queries share the same transaction
```

## Soft delete

Helpers for soft-deleting rows using a `deleted_at` timestamp column. See [soft-delete.md](soft-delete.md) for full documentation.

```python
from pw_core.db.soft_delete import soft_delete, restore, not_deleted

with transaction() as conn:
    soft_delete(conn, "items", "id", item_id, reason="duplicate")
    restore(conn, "items", "id", item_id)

# In queries:
sql = f"SELECT * FROM items WHERE category_id = :cid {not_deleted()}"
```

## Connectivity check

```python
from pw_core.db import transaction, ping

ping()  # raises if database is unreachable
```

## Testing

See [testing.md](testing.md) for database test fixtures (`pw_engine`, `pw_connection`, `pw_tables`).
