# Soft Delete

Helpers for soft-deleting database rows using a `deleted_at` timestamp column. Active rows have `deleted_at IS NULL`; deleted rows have a non-NULL timestamp set via SQLAlchemy's `func.now()` (database-native, dialect-independent).

## Functions

### `soft_delete(conn, table_name, id_column, id_value, *, reason=None)`

Mark a row as deleted by setting `deleted_at` to the current timestamp.

```python
from pw_core.db import transaction
from pw_core.db.soft_delete import soft_delete

with transaction() as conn:
    deleted = soft_delete(conn, "items", "id", item_id, reason="duplicate")
    # deleted is True if a row was updated, False if not found or already deleted
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Connection` | (required) | SQLAlchemy connection (inside a transaction) |
| `table_name` | `str` | (required) | Table name |
| `id_column` | `str` | (required) | Primary key column name |
| `id_value` | `Any` | (required) | Primary key value |
| `reason` | `str \| None` | `None` | Optional deletion reason |
| `deleted_at_column` | `str` | `"deleted_at"` | Timestamp column name |
| `reason_column` | `str \| None` | `"deleted_reason"` | Reason column name. Set to `None` if the table has no reason column. |

### `restore(conn, table_name, id_column, id_value)`

Restore a soft-deleted row by clearing `deleted_at` (and `deleted_reason` if present).

```python
from pw_core.db.soft_delete import restore

with transaction() as conn:
    restored = restore(conn, "items", "id", item_id)
```

Same parameters as `soft_delete` except no `reason` parameter.

### `not_deleted(*, column="deleted_at", prefix="AND ")`

Returns a SQL fragment for filtering active rows.

```python
from pw_core.db.soft_delete import not_deleted

# In a WHERE clause (default prefix "AND ")
sql = f"SELECT * FROM items WHERE category_id = :cid {not_deleted()}"
# → "SELECT * FROM items WHERE category_id = :cid AND deleted_at IS NULL"

# As the first condition
sql = f"SELECT * FROM items {not_deleted(prefix='WHERE ')}"
# → "SELECT * FROM items WHERE deleted_at IS NULL"

# Bare fragment
sql = f"SELECT * FROM items WHERE {not_deleted(prefix='')}"
# → "SELECT * FROM items WHERE deleted_at IS NULL"
```

## Schema convention

Tables using soft delete should have:

```sql
deleted_at    TIMESTAMP NULL DEFAULT NULL,  -- or TEXT for SQLite
deleted_reason VARCHAR(255) NULL DEFAULT NULL,  -- optional
```

Index `deleted_at` for query performance — most queries filter on `deleted_at IS NULL`:

```sql
CREATE INDEX idx_items_deleted_at ON items(deleted_at);
```

For tables with frequent filtered queries, a composite index is better:

```sql
CREATE INDEX idx_items_active_category_date
    ON items(deleted_at, category_id, created_at, id);
```

## Migrating from consumer projects

**Example** — replace a hand-rolled `soft_delete_item()` in a repo module:

```python
# Before
def soft_delete_item(item_id: str, reason: str | None = None) -> bool:
    with transaction() as conn:
        result = conn.execute(
            text("UPDATE items SET deleted_at=NOW(6), deleted_reason=:reason ..."), ...
        )
        return result.rowcount > 0


# After
from pw_core.db.soft_delete import soft_delete


def soft_delete_item(item_id: str, reason: str | None = None) -> bool:
    with transaction() as conn:
        return soft_delete(conn, "items", "id", item_id, reason=reason)
```
