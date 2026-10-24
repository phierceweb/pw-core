---
description: The app/ and src/<pkg> layouts, when a layer directory is earned, the file-size gate, and the two-package dependency pin
alwaysApply: true
---
# Project Structure

pw-core projects come in two shapes — pick by what the project *is*:

- **Full-stack web app** — the `app/` layout below: FastAPI + DB + web layers, for a project that serves HTTP and owns a database.
- **Library / tool** — the `src/<pkg>/` layout further down: a CLI, batch pipeline, or importable library with no web/DB layers.

Both share the same conventions and the same structural gate (`python -m pw_foundation.guards`, reading `.pw-guards.toml` — set `root` to `app` or `src/<pkg>` in that file).

## Full-stack web app layout (`app/`)

```
my_project/
├── app/                        # Application code
│   ├── __init__.py             # FastAPI app factory (create_app + router registration)
│   ├── __main__.py             # uvicorn entry point
│   ├── config.py               # Project-specific AppConfig subclass
│   ├── api/                    # Web layer — routes only
│   │   ├── __init__.py
│   │   ├── _templates.py       # Jinja2 setup (calls pw_core.web.templates.setup_templates)
│   │   ├── _util.py            # Request helpers (parse query params, etc.)
│   │   └── pages.py            # HTML page routes (split by domain if large)
│   ├── cli/                    # CLI entry points (thin)
│   │   ├── __init__.py
│   │   └── ...
│   ├── orchestrators/          # Multi-step workflows (optional)
│   │   └── ...
│   ├── services/               # Business logic (SRP per file)
│   │   └── ...
│   ├── repo/                   # Data access (SQLAlchemy via pw_core.db)
│   │   └── ...
│   ├── clients/                # External API wrappers (extend pw_core.clients)
│   │   └── ...
│   ├── templates/              # Jinja2 templates
│   │   ├── shared/             # Base layout, macros, error page
│   │   │   ├── base.html
│   │   │   └── error.html
│   │   └── pages/              # Page templates (one dir per domain)
│   └── static/                 # CSS, JS, images
├── alembic/                    # Database migrations
│   ├── env.py
│   └── versions/
├── config/                     # YAML configs (prompts, domain config)
├── .ai/                        # AI assistant context
│   ├── rules/                  # Copied/adapted from pw-core .ai/rules/
│   ├── plans/
│   └── docs/
├── .env                        # Environment variables (not committed)
├── .env.example                # Template for .env
├── .gitignore
├── CLAUDE.md                   # AI assistant instructions for this project
└── pyproject.toml              # Dependencies (includes pw-core)
```

## Library / tool layout (`src/`)

For a CLI, batch pipeline, or importable library — no web or DB layers. This is the shape pw-core's own batch/CLI consumers use.

```
my_tool/
├── src/<pkg>/                  # Importable package (src-layout)
│   ├── __init__.py
│   ├── cli.py                  # Entry / command dispatcher (thin)
│   ├── config.py               # Project-specific AppConfig subclass
│   ├── <domain>/               # One package per domain concern
│   │   ├── __init__.py
│   │   ├── model.py            # Domain model + reference data (flat if 1–2 files)
│   │   └── services/           # Single-domain operations (the bulk of the code)
│   ├── orchestrators/          # Multi-step workflows — added when one lands
│   └── utils/                  # Shared helpers — empty until earned
├── config/                     # YAML configs (prompts, model_router.yaml, …)
├── tests/
├── .ai/                        # rules / plans / docs (rules copied from pw-core)
├── .env.example
├── .gitignore
├── CLAUDE.md
└── pyproject.toml              # pw-core dep + extras (no [web]/[db] needed)
```

No `api/`, `repo/`, `clients/`, `templates/`, or `alembic/` — a library doesn't serve HTTP or own a schema. (Grows a DB? add `repo/` + the `[db]` extra. Serves HTTP? it's really the `app/` layout.)

**Grow a layer dir only when it earns one.** Start flat — a single `foo.py`, not a `foo/` package wrapping one file. Promote `<domain>/services/`, `orchestrators/`, or `utils/` to a directory once it holds two or more files; a lone module stays a flat file. This keeps small tools from carrying empty scaffolding.

## Key conventions

- **One concern per file.** Services, repo modules, and route files each own one domain.
- **Config in one place.** Subclass `pw_core.config.AppConfig` in `app/config.py`.
- **No `sys.path` hacks.** Use proper package imports via pyproject.toml.
- **Templates organized by feature.** `templates/pages/`, `templates/config/`, etc.

## File size limits

File-size budgets are by layer-directory name. Under an `app/` tree the build gate (`python -m pw_foundation.guards`) enforces a per-layer hard limit, warning at a fixed fraction of it; `src/<pkg>/` library layouts get the flat soft/hard gate (a library has no `app` layers). **The canonical limit values are code, not this doc:** `LAYER_DEFAULTS`, `UTIL_LIMIT`, and `SOFT_FRACTION` in `pw_foundation/guards/config.py` (flat defaults on `GuardsConfig`).

| Layer | Action when over its limit |
|-------|---------------------------|
| Service (`app/services/`) | Split by concern: `{domain}_{concern}.py` (e.g. `report_summaries.py`, `report_exports.py`) |
| Repo (`app/repo/`) | Split by entity or operation type |
| Orchestrator (`app/orchestrators/`) | Extract logic into services; orchestrator keeps coordination only |
| API route (`app/api/`) | Split by resource or page group |
| CLI (`app/cli/`) | One command per file, thin wrapper over service/orchestrator |
| `_util.py` files | Split by subdomain into `_catalog.py`, `_resolvers.py`, etc. |

When splitting a file:
- Name the new files `{domain}_{concern}.py` — not `{domain}_2.py` or `{domain}_helpers.py`
- Update the parent `__init__.py` to re-export if needed
- `grep -r` the old import paths to catch all callers

## Dependency on the framework

Pin both packages in `pyproject.toml` — a pw-core consumer imports `pw_foundation.*` directly:
```toml
[project]
dependencies = [
    "pw-foundation~=1.0",          # 1.x: minor releases are additive
    "pw-core[<extras>]~=0.28.0",   # the current release line (see its CHANGELOG)
]
```

During co-development, sibling checkouts override the pins:
```bash
pip install -e ../pw-foundation -e ../pw-core
```
