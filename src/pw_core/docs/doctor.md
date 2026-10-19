# Doctor — pw-core's rows

`pw-doctor` is pw-foundation's ([doctor.md](https://github.com/phierceweb/pw-foundation/blob/main/src/pw_foundation/docs/doctor.md)): a read-only attestation of the installed framework — which copy of each package is loaded, the interpreter, `LOG_*`, consumer wiring. When pw-core is installed it appends the rows `pw_core.doctor.checks()` returns, under their own groups. Nothing here writes, migrates or imports consumer code; `--db` opens one connection and closes it.

| Row | What it reports |
|---|---|
| `extras.available` | Which pw-core extras are present, by marker module (`importlib.util.find_spec` only — nothing is imported): `http`, `validate`, `cli`, `llm`, `db`, `web`, `anthropic`, `redis`, `ratelimit`, `articles`, `jsonschema`, `image-phash`. |
| `env.pw-core` | The env vars pw-core features read — `DATABASE_URL`, `MODEL_ROUTER_CONFIG`, `REDIS_URL`, `CACHE_CONFIG`, `WEB_HOST`, `WEB_PORT`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `BRAVE_API_KEY`, `REQUEST_TIMEOUT`, `PW_VERIFY_TLS`, `URL_CHECK_VERIFY_TLS`, `URL_FETCH_ALLOW_PRIVATE` — as the app would see them: a `.env` in the working directory is loaded first (shell values win) and the row names which. Secrets are redacted; the `PF_*` spellings are honoured ([config.md](config.md)). pw-foundation's own `env.resolution` row covers `LOG_LEVEL` and `LOG_FILE`. |
| `router.config` | SKIP when no router config exists at the resolved path, FAIL when it does not load, otherwise the agent names and the default client ([model-router.md](model-router.md)). |
| `deps.versions` | Installed versions of anthropic, httpx, pydantic, sqlalchemy, fastapi, structlog, tenacity and typer; absent ones are omitted. |
| `db.extra` (`--db`) | SKIP with the install command when `[db]` is missing. |
| `db.url` (`--db`) | `DATABASE_URL` redacted; FAIL when it is unset or does not parse. |
| `db.connect` (`--db`) | `SELECT 1` with a short connect timeout. A SQLite path that does not exist is a FAIL, not a create. |
| `db.migrations` (`--db`) | The alembic revision in the database against the script head when `alembic/` is in the working directory; SKIP otherwise, WARN when they differ or the table is missing. |

```bash
pw-doctor         # pw-foundation's rows, then these
pw-doctor --db    # adds the db group
```

## See also

- [config.md](config.md) — the settings the `env.pw-core` row reports.
- [database.md](database.md) — `DATABASE_URL` and the connection settings the db group probes.
