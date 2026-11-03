# Config

`pw_core.config.AppConfig` is pw-foundation's [`AppConfig`](https://github.com/phierceweb/pw-foundation/blob/main/src/pw_foundation/docs/config.md) plus the settings pw-core's database, LLM and web layers read. Subclass it in each project and add typed class attributes; resolution order (explicit override > environment > YAML > class default), type coercion, `.env` loading, YAML and dict-style access are the base class's and are documented there.

```python
from pw_core.config import AppConfig


class Config(AppConfig):
    GREETING: str = "hello"  # env var GREETING


cfg = Config()
cfg.DATABASE_URL, cfg.WEB_PORT, cfg.GREETING
```

## Framework settings

On top of the base's `LOG_LEVEL`, `LOG_FILE`, `APP_NAME` and `APP_URL`:

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `DATABASE_URL` | `str` | `""` | SQLAlchemy database URL |
| `REDIS_URL` | `str` | `""` | Redis connection URL |
| `OPENROUTER_API_KEY` | `str` | `""` | OpenRouter API key |
| `OPENROUTER_BASE_URL` | `str` | `"https://openrouter.ai/api/v1"` | OpenRouter API base URL |
| `OPENROUTER_PROVIDER_IGNORE` | `list[str]` | `[]` | Providers to exclude from routing |
| `WEB_HOST` | `str` | `"127.0.0.1"` | Web server bind address |
| `WEB_PORT` | `int` | `8000` | Web server bind port |
| `CORS_ORIGINS` | `list[str]` | `[]` | Allowed CORS origins, comma-separated. A `*` entry is refused at app startup — see [web](web.md#cors) |
| `REQUEST_TIMEOUT` | `int` | `120` | Per-request socket timeout (seconds) |
| `THREAD_MAX_WORKERS` | `int` | `4` | Thread pool size for parallel operations |
| `API_RATE_LIMIT_PER_MINUTE` | `int` | `60` | Default API rate limit |
| `MAX_PER_PAGE` | `int` | `200` | Upper bound for `per_page` in paginated endpoints |
| `ID_LENGTH` | `int` | `12` | Default nanoid length for `generate_id()` (clamped 8–36) |

## Framework env vars (not AppConfig fields)

These are read directly from the environment by framework modules — not declared on `AppConfig`. The tables here list the cross-cutting ones; a module's own variables (router, clients, URLs, jobs, eval, database connection) are documented with that module:

| Variable | Default | Module | Description |
|----------|---------|--------|-------------|
| `CACHE_CONFIG` | `config/cache.yaml` | `pw_core.llm.cache` | Path to the LLM response cache config YAML |
| `CACHE_CONFIG_RELOAD_SECONDS` | `60` | `pw_core.llm.cache` | How often to reload the cache config |
| `BUDGET_CONFIG` | `config/budgets.yaml` | `pw_core.budget` | Path to the budget config YAML |
| `BUDGET_CONFIG_RELOAD_SECONDS` | `300` | `pw_core.budget` | How often to reload the budget config |
| `BUDGET_ENFORCEMENT_DISABLED` | *unset* | `pw_core.budget` | When `true`, `check_budget()` short-circuits to always-pass (incident kill-switch) |
| `ANTHROPIC_CONNECT_TIMEOUT_S` | `10` | `pw_core.clients.anthropic` | Seconds allowed to connect, within the request timeout; `0` or less = no separate bound |

## Framework `PW_*` env vars

The `PF_*` spellings of these names are not read; `pw-doctor` flags a still-set one for every name pw-core reads (all but `PW_JOB_ID`, which it only exports).

| Name | Read by |
|---|---|
| `PW_VERIFY_TLS` | outbound HTTP (`pw_core.utils.http_tls`, `pw_core.fetch`) |
| `PW_FETCH_UA` | `pw_core.fetch` User-Agent |
| `PW_ARTICLE_WAYBACK_FALLBACK` | `pw_core.utils.article_fetch` |
| `PW_ARTICLE_EXTRACTOR_LOG_LEVEL` | `pw_core.utils.article_fetch` |
| `PW_CORE_CLAUDE_CODE_MODEL` | `pw_core.clients.claude_code` |
| `PW_TEST_DATABASE_URL` | the pytest plugin's server-database fixtures |
| `PW_TEST_LOCK_TIMEOUT_S` | the pytest plugin's server-database fixtures |
| `PW_JOB_ID` | exported to job subprocesses |

`pw-doctor`'s `env.pw-core` row reports the effective values ([doctor.md](doctor.md)).

## See also

- The project's `config-driven` rule — tunables come from the environment, not call sites.
- [web.md](web.md) — `CORS_ORIGINS`, `API_RATE_LIMIT_PER_MINUTE` and `MAX_PER_PAGE` at the API boundary.
