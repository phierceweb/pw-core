# LLM Cache

`pw_core.llm.cache` — response cache for LLM calls. Answers "did we already ask the LLM exactly this?" before making an expensive call.

It is an **exact cache**, keyed by `input_hash` (SHA256 of model + prompts + sampling + configs) and agent type. An identical call reuses the stored response without a round-trip; a call that differs in any byte of the prompt misses.

**Not** to be confused with `pw_core.cache.redis` (Redis KV cache for API response memoization). This module caches LLM call responses in the database.

## Quick start

```python
from pw_core.llm.cache import cache_lookup, cache_store, record_cache_hit
from pw_core.llm.tracking import compute_input_hash, track_run
from pw_core.llm.router import get_agent_config
from pw_core.clients.openrouter import get_client


@track_run(agent_type="classifier", provider="openrouter")
def _tracked_chat(*, model, messages, **sampling):
    return get_client().chat(model=model, messages=messages, **sampling)


def classify(*, text: str) -> dict:
    cfg = get_agent_config("classifier")
    messages = [{"role": "user", "content": text}]

    input_hash = compute_input_hash(model=cfg["model"], messages=messages, sampling=cfg)

    # 1. Check cache first
    hit = cache_lookup(agent_type="classifier", input_hash=input_hash)
    if hit is not None:
        record_cache_hit(hit=hit)
        return hit.parsed_output

    # 2. Live call
    content, usage = _tracked_chat(messages=messages, **cfg)
    run_id = usage["_llm_run_id"]

    # 3. Store response
    parsed = parse_llm_json(content)
    cache_store(
        agent_type="classifier",
        input_hash=input_hash,
        source_run_id=run_id,
        model=cfg["model"],
        parsed_output=parsed,
        raw_response=content,
        usage=usage,  # refuses the store when finish_reason says truncated
    )
    return parsed
```

## Cache config (cache.yaml)

Per-agent policy. Place in `config/cache.yaml` (or set `CACHE_CONFIG` env var).

To disable caching entirely with no config file, set `CACHE_CONFIG` to `off` (also accepted: `disabled`, `none`, `0`) — exact caching turns off for every agent. This is what test suites should use (`hermetic_test_env()` sets it — see [testing.md](testing.md)); a missing file, by contrast, means framework defaults, which have `exact: true`.

The loader never raises on a bad config. A missing file means framework defaults. A file that exists but fails to parse (a repeated key included — see [yaml-utils](https://github.com/phierceweb/pw-foundation/blob/main/src/pw_foundation/docs/yaml-utils.md)), or whose top level is not a mapping, logs a warning (`cache_config_load_failed` / `cache_config_not_a_mapping`) and turns caching off, since the unreadable file may be what disabled it. A `defaults` or `agents` section that is not a mapping turns caching off for every agent, and an `agents.<slug>` entry that is not a mapping turns it off for that agent (`cache_config_section_not_a_mapping`). `exact` accepts YAML booleans and their quoted forms (`"false"`, `"no"`, `"off"`, `"0"` and the true counterparts); any other value turns caching off (`cache_config_invalid_flag`).

```yaml
# config/cache.yaml

defaults:
  exact: true
  ttl_seconds: 86400        # 24 hours; 0 = never expire
  on_miss: proceed           # 'proceed' | 'warn_log'

agents:
  classifier:
    exact: true
    ttl_seconds: 604800       # 7 days — classifications are stable

  extractor:
    exact: true
    ttl_seconds: 3600         # 1 hour — extracted values can go stale

  summarizer:
    exact: true
    ttl_seconds: 7200

  critic:
    exact: true
    ttl_seconds: 0            # no TTL — output is reproducible forever
```

### Config keys

| Key | Default | Description |
|-----|---------|-------------|
| `exact` | `true` | Check exact-hash cache |
| `ttl_seconds` | `86400` | Entry expiration; `0` = never |
| `on_miss` | `proceed` | `warn_log` emits a WARNING on miss (useful during rollout) |

There is no size-based eviction: the cache is bounded by `ttl_seconds` plus the explicit invalidation helpers below.

**Ignored keys.** A config written for an older release still loads; these keys do nothing and each logs `cache_config_ignored_key` once per process: `max_entries_per_agent`, and the semantic-cache keys `semantic`, `semantic_threshold`, `semantic_embedding_model` and `canonicalize`. Delete them from `cache.yaml` to silence the warning.

## compute_input_hash()

```python
from pw_core.llm.tracking import compute_input_hash

h = compute_input_hash(
    model="anthropic/claude-opus-4-7",
    messages=[{"role": "user", "content": "classify this text"}],
    sampling={"temperature": 0.0, "max_tokens": 512},
    configs={"category_set_id": 42},  # optional project-specific snapshot
)
# → 64-char hex string
```

The value written to `llm_runs.input_hash`. Use it as the primary key for both the exact cache lookup and the tracking row.

**Accepts** either `messages` list or pre-rendered `rendered_system` / `rendered_user` strings. Non-sampling keys in the `sampling` dict (e.g. `model`, `agent_type`) are automatically stripped before hashing.

**Beyond the rendered prompts.** Rendering keeps only plain-string `system`/`user` content, so a `messages` list carrying anything else — multi-part (vision) content, assistant/tool turns, `tool_calls`, a `name` field — additionally keys the hash by a digest of the whole list. Without it, every vision call on one model collapses to a single key and the cache returns page 1's answer for pages 2..N. A list of plain `{role, content}` system/user string messages hashes on the rendered prompts alone, with no digest.

A part that isn't JSON data (an open file handle, a client object) is stringified rather than rejected. Objects without a stable `repr` therefore produce a per-process key that never hits across processes — a lost cache, not a wrong answer.

**Agreement with `record()`.** `LlmRunRepo.record()` autocomputes an omitted `input_hash` from the `rendered_prompts` it is given, and cannot see the rest of the list. `llm_step`, `tracked_messages_call`, and `@track_run` all key off `messages` for you; a call site that invokes `record()` directly must pass `input_hash=compute_input_hash(messages=...)` or its rows will not be findable by the cache key.

## cache_lookup()

```python
from pw_core.llm.cache import cache_lookup, CacheHit

hit: CacheHit | None = cache_lookup(agent_type="classifier", input_hash=h)
```

Returns `None` on miss or when caching is disabled for the agent. On hit, returns a `CacheHit`:

| Field | Type | Description |
|-------|------|-------------|
| `entry_id` | `int` | PK of `llm_cache_entries` |
| `parsed_output` | `Any` | Cached parsed JSON value; NaN and ±Infinity come back as `None` |
| `raw_response` | `str \| None` | Cached raw LLM response |
| `source_run_id` | `int` | FK to original `llm_runs` row |
| `model` | `str` | Model name from the source run |
| `agent_type` | `str` | Agent slug |
| `input_hash` | `str` | The SHA256 key |
| `hit_type` | `str` | Always `"exact"` |
| `similarity` | `float` | Always `1.0` |
| `created_at` | `datetime` | When the cache entry was created |

## cache_store()

```python
from pw_core.llm.cache import cache_store

cache_store(
    agent_type="classifier",
    input_hash=h,
    source_run_id=run_id,
    model="anthropic/claude-opus-4-7",
    parsed_output={"label": "tech"},
    raw_response='{"label": "tech"}',
    usage=usage,  # pass the client usage dict — the default form
)
```

No-op when exact caching is disabled for the agent.

**Truncation guard.** A cache hit replays stored text with no finish reason, so a truncated response written to the cache would lose its flag forever and replay incomplete on every hit. `cache_store` therefore refuses a known-truncated store itself — pass the client `usage` dict and it derives truncation from `usage["finish_reason"]` (via [`truncated_from_usage`](llm-parse.md#detecting-truncation-truncated-not-the-text)); an explicit `truncated=` overrides. A refused store logs `cache_store_skipped_truncated` and returns. Unknown truncation (no `usage`, no `truncated`, or an unrecognised finish reason) is stored. `llm_step` forwards `usage=` for you. A store over an existing `input_hash` — a concurrent identical request, or a re-store after the entry expired — refreshes that row in place through the portable [`upsert`](db-upsert.md) helper: same id, same `created_at`, same `hit_count`/`last_hit_at`, new response and expiry. Since `input_hash` covers model, prompts, sampling, and configs, a conflicting row is the same logical request.

## record_cache_hit()

Records a zero-cost `llm_runs` row so analytics see every "call that happened":

```python
from pw_core.llm.cache import record_cache_hit

hit_run_id = record_cache_hit(hit=hit, duration_ms=2)
```

Creates:
- `llm_runs` row: `status='cache_hit'`, zero tokens, zero cost
- `llm_run_links` row: `parent_run_id=source_run_id`, `relation='cache'`
- `llm_run_tags` rows: `cache:exact` and `cache_age:<bucket>`

## Invalidation

```python
from pw_core.llm.cache import by_agent, by_model, by_run, purge_expired

by_agent("classifier")  # drop all classifier entries
by_model("extractor", new_model="claude-opus-4-7")  # drop entries for other models
by_run(run_id=1042)  # drop the entry from one run
purge_expired()  # sweep rows past expires_at
```

All functions return the count of deleted rows.

### When to invalidate

| Trigger | Invalidation |
|---------|-------------|
| Prompt version bump for an agent | `by_agent(agent_type)` — conservative; keeps cache warm if output is stable |
| Model swap for an agent | `by_model(agent_type, new_model=...)` — drops old-model entries only |
| Bad answer reported | `by_run(run_id)` — surgical, one entry |
| Maintenance sweep | `purge_expired()` — periodic, automated |

## Schema

### `llm_cache_entries`

Registered on the shared `pw_core.llm.tracking.schema.metadata`, and the only table in `ALL_CACHE_TABLES`. Created by the consumer's Alembic migration.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INT PK | |
| `input_hash` | CHAR(64) | SHA256 hex — unique with `agent_type_id` |
| `agent_type_id` | SMALLINT FK | → `llm_agent_types.id` |
| `model_id` | SMALLINT FK | → `llm_models.id` |
| `source_run_id` | BIGINT FK | → `llm_runs.id` ON DELETE CASCADE |
| `parsed_output` | JSON | Denormalized for fast hit reads |
| `raw_response` | MEDIUMTEXT | Denormalized for fast hit reads |
| `hit_count` | INT | Hit counter — analytics only, nothing evicts on it |
| `last_hit_at` | TIMESTAMP | Analytics only |
| `expires_at` | TIMESTAMP NULL | NULL = never |
| `created_at` | TIMESTAMP | |

Unique key: `(input_hash, agent_type_id)` — `uq_llm_cache_input_hash_agent`.
`input_hash` covers model, prompts, sampling and configs but **not** the agent, so
keying on it alone let one agent be served another's stored response.

**Consumer migration (0.22.0):** drop `uq_llm_cache_input_hash` and add
`uq_llm_cache_input_hash_agent` over `(input_hash, agent_type_id)`. Widening a
unique key never conflicts with existing rows; the first call from each other
agent misses once and stores its own entry.

**Consumer migration (0.26.0) — `llm_embeddings`:** pw-core no longer defines this table.
`framework_ddl()` and `metadata.create_all()` don't create it, and a migration that
creates the cache tables from `ALL_CACHE_TABLES` creates only `llm_cache_entries` on
a fresh database. An existing `llm_embeddings` stays until the consumer drops it in
its own migration; it references `llm_cache_entries`, so drop it first. A consumer
migration, downgrade or maintenance helper that names `llm_embeddings` must tolerate
its absence on a database built fresh from migrations, and a downgrade that loops
over `ALL_CACHE_TABLES` on an existing database must drop `llm_embeddings` first
when it exists.

## Analytics queries

### cache_stats() — the key-volatility diagnostic

```python
from pw_core.llm.cache import cache_stats

rows = cache_stats()  # every agent with entries or runs
rows = cache_stats(agent_type="classifier")
rows = cache_stats(since=utc_cutoff(days=7))  # bound the run half
```

Pass `since=` on any polled surface. Unbounded, the run half's
`COUNT(DISTINCT input_hash)` grows with lifetime run count, so a dashboard
refreshing `/api/cache.json` re-scans the whole history each time; the admin
pages pass their own window.

One dict per agent slug: `agent_type`, `entries` (`llm_cache_entries` count), `hits` (sum of `hit_count`), `entries_hit` (entries with at least one hit), `last_hit_at`, `live_runs` (`llm_runs` rows with `status='success'`), `distinct_hashes` (distinct non-null `input_hash` among those runs).

**The reading that matters:** `entries` ≈ `live_runs` with `hits` ≈ 0 means every call minted a fresh key — a cache that structurally cannot hit (e.g. a prompt interpolating the current date), which is otherwise indistinguishable from a cold cache. The admin `/cache` page renders this table; `llm_step(dry_run=True)` ([llm-step.md](llm-step.md#dry-run)) answers the per-call form of the same question.

```sql
-- Cache hit rate last 7 days by agent type
SELECT at.slug,
       SUM(CASE WHEN r.status = 'cache_hit' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS hit_rate,
       COUNT(*) AS calls
FROM llm_runs r
JOIN llm_agent_types at ON at.id = r.agent_type_id
WHERE r.created_at >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY at.slug
ORDER BY hit_rate DESC;

-- Cost avoided by cache (what hits would have cost at source-run rates)
SELECT at.slug,
       COUNT(*) AS hits,
       SUM(source.cost_usd) AS cost_avoided_usd
FROM llm_runs hit
JOIN llm_run_links l    ON l.child_run_id  = hit.id AND l.relation = 'cache'
JOIN llm_runs source    ON source.id       = l.parent_run_id
JOIN llm_agent_types at ON at.id = hit.agent_type_id
WHERE hit.status = 'cache_hit'
  AND hit.created_at >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY at.slug;
```

## Env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_CONFIG` | `config/cache.yaml` | Path to the cache config YAML file |
| `CACHE_CONFIG_RELOAD_SECONDS` | `60` | How often to reload config from disk |

## Consumer migration

Enabling the exact cache for a consumer project requires:

1. **Create the Alembic migration** — importing `pw_core.llm.tracking.metadata` in `env.py` is sufficient; `llm_cache_entries` will be detected by autogenerate.

2. **Add env vars** to `.env.example`:
   ```
   CACHE_CONFIG=config/cache.yaml
   ```

3. **Create `config/cache.yaml`** with per-agent policy (see example above).

4. **Update each service** to call `cache_lookup` before the LLM call and `cache_store` after (see Quick start above).

No `pyproject.toml` changes needed — the cache module uses only dependencies already declared in `pw-core`.

## See also

- [`docs/llm-tracking.md`](llm-tracking.md) — `input_hash` computation and the tracking backbone this cache sits on top of
- [`docs/model-router.md`](model-router.md) — per-agent model + sampling config (cache lookup happens before the router call)
- [`docs/cache.md`](cache.md) — Redis KV cache (different use case: API response memoization)
