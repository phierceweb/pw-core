# Installation

How to install `pw-core` for local development, deploy from PyPI, and pick the right combination of extras for your project's shape.

Formerly published as pf-core (0.2.3–0.26.0); renamed at 0.27.0. The old PyPI releases remain but receive no updates.

`pw-core` builds on [pw-foundation](https://github.com/phierceweb/pw-foundation) — structured logging, the exception hierarchy, the config base and env resolvers, pure utilities, the `Service` base and the `pw-guards` build gate, on five small dependencies — which `pip install pw-core` brings in automatically. The default install adds pw-core's own dependency-light modules; everything else — LLM clients + anti-slop guards, HTTP utils, CLI scaffolding, the database layer, the FastAPI web layer, the job tracker, eval harness and admin dashboard — ships as **opt-in extras** that compose orthogonally. A project that needs only the foundation installs `pw-foundation` alone; an LLM script can `pip install pw-core[llm]` without dragging in a database or web server.

## Quick reference

| Scenario | Command |
|----------|---------|
| Foundation only — logging/exceptions/config/utils and the build gate, no pw-core | `pip install pw-foundation` |
| pw-core's base — pw-foundation plus the dependency-light core modules, no LLM/HTTP | `pip install pw-core` |
| Validate LLM output only — anti-slop guards, no clients | `pip install pw-core[validate]` |
| Lightweight LLM tool — clients + anti-slop guards | `pip install pw-core[llm]` |
| Crawl/fetch web pages — title, body, publish date, liveness | `pip install pw-core[crawl]` |
| Active pw-core development on this machine | `pip install -e ~/projects/pw-core` |
| New consumer project, full app framework | Pin `pw-core[full,<dialect>]~=0.27.0` in `pyproject.toml`, then `pip install -e .` |
| Fresh machine, consumer only | `git clone <project>` → `pip install -e .` (pulls pw-core from PyPI automatically) |
| Fresh machine, also editing the framework | Clone both → `pip install -e ~/projects/pw-foundation -e ~/projects/pw-core` (overrides the PyPI pins) |

**Starting a new consumer from scratch?** From a pw-core checkout, `bin/new-consumer <name> --layout {lib|app}` stamps a runnable, conformant project with both pins, `bin/` wrappers, and a day-1 slice — see [scaffold.md](scaffold.md). Add `--base-only` for a project that needs only pw-foundation.

**Wiring an existing consumer?** From the project root, `pw-setup` (installed with pw-foundation) links both packages' bundled docs at `docs/pw-foundation/` and `docs/pw-core/`, installs the starter rules into `.ai/rules/` and points `.claude/` and `.cursor/` at `.ai/`. Idempotent; it never replaces a real file or directory (it reports and exits 1 instead). `pw-doctor` reports the link read-only (the `wiring.docs_link` row).

## Local development (recommended while actively working on pw-core)

Editable installs mean changes to the framework are picked up immediately with no reinstall:

```bash
cd ~/projects/my-project
pip install -e ~/projects/pw-foundation -e ~/projects/pw-core
```

Edit any file in either checkout, and every project using it sees the change on the next import. (Installing only pw-core editable leaves pw-foundation on the PyPI wheel; `pw-doctor`'s `copy.mixed` row flags that.)

## From PyPI (deploy or fresh machine)

Pin a compatible release in your project's `pyproject.toml`:

```toml
dependencies = [
    "pw-foundation~=1.0",
    "pw-core[full,postgres]~=0.27.0",
]
```

Then `pip install -e .` pulls both from PyPI automatically (pw-core requires pw-foundation, but a consumer imports `pw_foundation.*` directly, so it declares its own pin). To track unreleased work on `main`, pin `"pw-core[full,postgres] @ git+https://github.com/phierceweb/pw-core.git@main"` instead.

## Choosing extras

Pick the lowest tier that covers what you actually use. Extras compose — you can add more later without restructuring.

### Tier 0 — base install (no extras)

```bash
pip install pw-core
```

pw-foundation's five deps (structlog, python-dotenv, pyyaml, nanoid, rich) and nothing more — no httpx/pydantic/LLM stack. Includes:

- everything pw-foundation ships, imported as `pw_foundation.*` (never through pw-core): `log`, `exceptions`, `config.AppConfig`, `parallel`, `output`, `services.Service`, and `pw_foundation.utils.{dates,ids,hashing,json,yaml,slugify,throttle,env,io,config_path,periods,reload_cache}`
- `pw_core.config.AppConfig` — pw-foundation's config base plus the framework settings (`DATABASE_URL`, `OPENROUTER_*`, `WEB_*`, …)
- `pw_core.utils.*` pure-Python helpers (similarity, vocab, relative dates, json recovery)
- `pw_core.parsers` (HTML primitives)
- `pw_core.budget.check_budget`, `pw_core.budget.project_cost` (in-memory mode — falls back to 0.0 projection without `[db]` cost rates)
- `pw_core.utils.json_recovery` — extract/recover JSON from messy text (markdown fences, trailing prose, mid-stream truncation); generic and stdlib-only

Use this for: a project that wants the framework's `AppConfig` settings, parsers or budget guards without LLM or web dependencies. A project that only needs logging, exceptions, config/env discipline and `Service` installs `pw-foundation` alone.

Not included at this tier — `pw_core.llm.*` is the **LLM surface**, not foundation: `pw_core.llm.parse` / `pw_core.llm.validate` (the anti-slop output guards) need `[validate]` (raise a friendly `ImportError`); `pw_core.clients.*` need `[llm]`; `pw_core.llm.tracked` needs `[tracking]`. The remaining `pw_core.llm` members (`prompts`, `router`, `url_check`, `safe_apply`) are pyyaml/stdlib-only so they happen to import without an extra, but they belong to the LLM tier — install `[validate]` (or any `[llm]`+ tier) when you use them. `pw_core.utils.urls` / `pw_core.utils.url_liveness` need `[http]`; `pw_core.cli.create_cli` needs `[cli]`.

### Tier 1 — capability extras

Each unlocks one tier of pw-core's framework surface. They compose orthogonally — `[validate]` (anti-slop guards) without httpx/clients, `[db]` without LLM, `[web]` without `[db]`, `[llm]` standalone. Mix and match as needed.

| Extra | Adds | Unlocks |
|---|---|---|
| `[validate]` | `json-repair`, `pydantic` | The anti-slop **output guards** with no client/HTTP stack: `pw_core.llm.parse` (+ json-repair recovery) and `pw_core.llm.validate` (pydantic schema/semantic validation). For validating LLM output, or bringing your own transport. |
| `[llm]` | `[validate]` + `httpx` (via `[http]`) + `tenacity` | `pw_core.clients.*` (OpenRouter / Brave / Claude Code) on top of `[validate]`. `[llm]` ⊇ `[validate]`. (`pw_core.llm.tracked` additionally needs `[tracking]` — it records to the DB.) |
| `[http]` | `httpx` | `pw_core.utils.urls`, `pw_core.utils.url_liveness` (URL liveness + Wayback checks) |
| `[cli]` | `typer`, `click` | `pw_core.cli.create_cli` — Typer command scaffolding |
| `[db]` | `sqlalchemy`, `alembic` | `pw_core.db.*`, `pw_core.alembic`, fully-functional DB-backed cost guards (`BudgetRepo`, `CostRateRepo`) |
| `[web]` | `fastapi`, `jinja2`, `uvicorn[standard]` | `pw_core.web.app_factory`, error pages, markdown, pagination, templates |
| `[jobs]` | (depends on `[db,cli,tracking]` + `pydantic`) | `pw_core.jobs.*` — state machine, step history, worker leases, `pw-jobs` CLI. `[tracking]` is not optional: the jobs schema shares metadata with the tracking tables so job-attribution foreign keys resolve. |
| `[tracking]` | (depends on `[db,llm]`) | `pw_core.llm.tracking.*`, `pw_core.llm.cache.*` — one DB row per LLM call, cache hit-rate analytics |
| `[admin]` | (depends on `[web,tracking]`) | `pw_core.web.llm_admin` — mountable admin dashboard |
| `[eval]` | (depends on `[tracking,jobs]` + `jinja2`) | `pw_core.eval.*` — golden-set replay, comparators, HTML report, `pw-eval` CLI |
| `[otel]` | (depends on `[tracking,cli]`) + `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` | `pw_core.llm.tracking.otel` and the `pw-export otel` CLI — backfill `llm_runs` to an OTLP/HTTP backend as GenAI spans ([otel.md](otel.md)). Not in `[full]`. |

**This table is enforced, not aspirational.** `pw_core._extras.required_extra()` records which extra each module needs in order to import, and `tests/test_extras_tiers.py` walks the source tree asserting no module-scope import escapes its own extra's closure (the closure is computed from the extras declared in `pyproject.toml`). A module that quietly reaches into a higher tier fails the build here rather than failing on a consumer's install, where the maintainer would never see it — a dev checkout has every extra. Note the map records each module's *minimum* import tier, which is occasionally lower than the extra you'd install for the feature: every `pw_core.jobs` module imports under `[tracking]`, and only the `pw-jobs` CLI needs `[cli]`.

### Tier 2 — driver and feature extras

| Extra | Adds | When to use |
|---|---|---|
| `[mysql]` | `pymysql` | MySQL/MariaDB consumers |
| `[postgres]` | `psycopg[binary]` | PostgreSQL consumers |
| `[redis]` | `dogpile.cache`, `redis` | Caching layer (graceful no-op without it) |
| `[ratelimit]` | `slowapi` (depends on `[web]`) | Per-IP rate limiting on FastAPI routes |
| `[jsonschema]` | `jsonschema` | Strict JSON Schema path in `pw_core.llm.validate` |
| `[articles]` | `trafilatura`, `htmldate`, `tenacity` | Article fetch + extract (`pw_core.utils.article_fetch`) |
| `[crawl]` | = `[http,articles]` | **Intent extra: "crawl/fetch web pages."** Title, body, publish date (`article_fetch`) + URL liveness/Wayback checks (`utils.urls`) in one named line — serving a front-end is a different intent (`[web]`) |
| `[anthropic]` | `anthropic` | Direct Anthropic SDK client (`pw_core.clients.anthropic`) — multimodal/vision, direct cache + input/output token reporting |
| `[image-phash]` | `ImageHash`, `Pillow` | Perceptual-hash image dedup (`pw_core.utils.phash`) |

SQLite needs no driver extra — it's part of stdlib.

### Tier 3 — meta-extra for the maximalist

```bash
pip install pw-core[full,postgres]
```

`[full]` is shorthand for `[db,web,llm,cli,jobs,tracking,admin,eval,redis,ratelimit,jsonschema]`. Add the dialect driver (`[mysql]` or `[postgres]`) and any optional capability extras (`[articles]`) separately.

### Tier 4 — dev / test

| Extra | Adds | When to use |
|---|---|---|
| `[dev]` | `pytest`, `httpx`, `factory-boy`, `ruff`, `mypy`, `pre-commit`, `build` | Tests, linting, and the build-gate hooks |
| `[test-containers]` | `testcontainers[mysql]` | Integration tests against a real MySQL container |

## Combining extras

```bash
pip install -e ".[full,postgres,articles]"
```

Comma-separated, no spaces. Quote the spec when using zsh.

## Example install profiles

Typical shapes, by project type. Anything importing `pw_core.clients` / `pw_core.llm` resolves to `[llm]` — `[full]` profiles transitively, the batch-pipeline profile via `[tracking]` (which is `[db,llm]`). Declaring `[llm]` explicitly is still good practice so each pyproject self-documents what it uses.

| Project shape | Install line |
|---|---|
| Full-stack web app, Postgres | `pw-core[full,llm,postgres]~=0.27.0` |
| Full-stack web app, MySQL + article ingest | `pw-core[full,llm,mysql,articles]~=0.27.0` (+ `[redis,ratelimit]` if caching/limits are used) |
| Full-stack web app, SQLite | `pw-core[full,llm]~=0.27.0` (SQLite driver is stdlib) |
| Batch document pipeline (no web/db) | `pw-core[image-phash,tracking,llm]~=0.27.0` |
| CLI on the Typer scaffold, no LLM | `pw-core[cli]~=0.27.0` |
| Foundation-only tool (no pw-core at all) | `pw-foundation~=1.0` alone — `bin/new-consumer --base-only` scaffolds one |

Every pw-core profile also declares `pw-foundation~=1.0`.

## Updating the dependency

### Day-to-day (local editable install)

If the framework is installed editable (`pip install -e`), there is nothing to do. Edit a file in either checkout, and every project using it sees the change immediately.

### Releasing a new version

When a change is stable and you want it on PyPI for other machines:

```bash
# 1. Bump the version in pyproject.toml (__version__ derives from it), commit
cd ~/projects/pw-core
git add -A && git commit -m "what changed"

# 2. Optional: rehearse on TestPyPI — the same build and test jobs as a release,
#    uploaded to test.pypi.org as X.Y.Z.devN (N = the workflow run number)
git push origin main
gh workflow run publish.yml --ref main

# 3. Tag the release and push — a v* tag triggers .github/workflows/publish.yml,
#    which runs every job in test.yml (Postgres and MySQL included), builds the
#    sdist/wheel, and uploads to PyPI only if all of them pass
git tag vX.Y.Z
git push origin main --tags

# 4. Bump the compatible-release pin in each consumer's pyproject.toml
#    "pw-core[full,postgres]~=0.27.0"

# 5. Reinstall in each consumer
cd ~/projects/my-project
pip install -U -e .
```

To smoke a rehearsal build, fetch only the wheel from TestPyPI so its dependencies resolve from PyPI, not from TestPyPI's unvetted names: `pip download --no-deps --index-url https://test.pypi.org/simple/ pw-core==X.Y.Z.devN -d rehearsal/ && pip install rehearsal/pw_core-*.whl`.

PyPI versions are immutable — a published version can never be replaced, so bump the version for every release (never re-tag an existing one). A `~=0.27.0` consumer picks up `0.27.x` patches on the next reinstall with no pin change; the next minor requires a deliberate pin bump. Patch fixes land on the newest minor only — once a new minor ships, older lines are frozen, so bumping the pin is how a consumer keeps receiving fixes. If OIDC trusted publishing isn't active for the run, publish manually from the pw-core checkout: `python -m build && twine upload -u __token__ dist/*`.

### On a fresh machine

```bash
git clone <project-repo>
cd <project>
python -m venv .venv && source .venv/bin/activate
pip install -e ".[full,postgres,redis]"   # whichever extras the project needs
```

pw-core is pulled from PyPI automatically via the version pin in `pyproject.toml`.

If you also need to develop pw-core locally on that machine:

```bash
git clone https://github.com/phierceweb/pw-foundation.git ~/projects/pw-foundation
git clone https://github.com/phierceweb/pw-core.git ~/projects/pw-core
pip install -e ~/projects/pw-foundation -e ~/projects/pw-core[full]   # overrides the PyPI installs with editable
```

## Verifying which copy is loaded

When patching pw-core, always verify the consumer is loading your edits and not a stale wheel from site-packages:

```bash
python -c "import pw_core; print(pw_core.__file__)"
# /Users/you/projects/pw-core/src/pw_core/__init__.py   ← editable, good
# /Users/you/.venv/lib/python3.12/site-packages/pw_core/...  ← installed copy, NOT being edited
```

If the path points into `site-packages`, you're editing files that aren't being imported. Reinstall with `pip install -e ~/projects/pw-core` to fix.

## Testing

```bash
cd ~/projects/pw-core
python3.12 -m venv .venv                          # 3.12+; skip if .venv already exists
.venv/bin/python -m pip install -e ".[full,dev,anthropic]"
bin/test                                          # args pass through to pytest
```

The `[dev]` extra installs pytest. The base fixtures `pw_app_client` and `pw_budget_disabled` are auto-registered as a pytest plugin via the `pw_core` entry point in `pyproject.toml` — no `conftest.py` import needed. The DB fixtures (`pw_engine`, `pw_connection`, `pw_tables`) require the `[db]` extra and are opt-in: add `pytest_plugins = ["pw_core.testing.db_fixtures"]` to your `conftest.py`. See [testing.md](testing.md).

The full test suite requires `[full,dev,anthropic]` — most fixtures and integration tests exercise the DB and web layers, and the Anthropic client tests patch the `anthropic` SDK, which is its own extra (not part of `full`). Without `[anthropic]`, those tests error instead of skipping.

### Verifying the bare install stays lean

The in-suite `tests/test_pyproject_tiers.py` guards the dependency *metadata* (base stays free of httpx/pydantic/typer/json-repair/tenacity; `[llm]`/`[tracking]`/`[full]` compose correctly). To verify the *actual* install behaviour — that a bare `pip install pw-core` resolves to pw-foundation and its five deps only (no httpx/pydantic/typer) and that gated modules raise a friendly `ImportError` — run:

```bash
python bin/verify-bare-install
```

It builds the wheel through the sdist (the route `publish.yml` takes, so a stale `build/` tree cannot leak in), installs it with no extras into a throwaway venv, and runs `bin/bare_install_smoke.py` inside it; a second venv checks `[validate]`. Run this after any change to the dependency tiers; CI runs the same smoke on every push (the `bare` job in `.github/workflows/test.yml`).
