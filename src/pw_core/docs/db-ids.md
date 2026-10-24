# Database IDs

`pw_core.db.ids.allocate_id` — a URL-safe nanoid that is verified unique against a table
before you insert it. Generation itself is pw-foundation's `generate_id`
([ids.md](https://github.com/phierceweb/pw-foundation/blob/main/src/pw_foundation/docs/ids.md)).

```python
from pw_core.db import transaction
from pw_core.db.ids import allocate_id

with transaction() as conn:
    order_id = allocate_id(conn, table="orders")  # fresh id
    slug_id = allocate_id(conn, table="pages", preferred="about-us")  # keep it if free
    short = allocate_id(conn, table="codes", column="code", size=8)
```

- `preferred` is returned as-is when no row holds it; otherwise a generated id is tried.
- `size` defaults to the `ID_LENGTH` environment variable (clamped to 8–36), else 12.
- `table` and `column` must be plain identifiers (`InvalidInputError` otherwise) because they
  are interpolated into the lookup SQL; the candidate id is bound as a parameter.
- After `max_attempts` (24) collisions it raises `PreconditionError`.

`allocate_id` reserves nothing; it only checks that the id is free when called. The caller inserts the row.

## See also

- [database.md](database.md) — `transaction()` and the repository base.
