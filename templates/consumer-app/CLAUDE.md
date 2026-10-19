# __NAME__ — AI assistant context

A full-stack **app-layout** project (FastAPI + SQLAlchemy), built on pw-foundation and pw-core.

## Rules

Project rules live in `.ai/rules/`, installed by `pw-setup` (which `bin/setup` runs) from both
packages: pw-foundation's (code style, config-driven design, error handling, logging,
framework-first, docs-sync, plans) and pw-core's (layering, anti-patterns, project-structure).
Read `layering.md` first — no layer imports from a layer above it. `.claude/rules` and
`.cursor/rules` point at them.

## Layout

- `app/__init__.py` — `create_app()` factory (`pw_core.web.app_factory`) + router registration.
- `app/api/` — routes only; each handler calls one service/orchestrator.
- `app/config.py` — `Config(AppConfig)` subclass of `pw_core.config.AppConfig`; the `cfg` singleton.
- Add `app/services/` (business logic), `app/repo/` (data access via `pw_core.db`),
  `app/orchestrators/` (workflows) as the app grows.

## Commands

`bin/setup`, `bin/web`, `bin/test`, `bin/lint`.

## The framework

Two packages, both declared in `pyproject.toml`:

- **pw-foundation** (`pw_foundation.*`) — logging, exceptions, the config base and env
  resolvers, pure utils (dates, ids, hashing, json, yaml, slugify, throttle), `Service`,
  parallel helpers, output reporters, and the `pw-guards` / `pw-doctor` / `pw-setup` tools.
  Module index: [docs/pw-foundation/modules.md](docs/pw-foundation/modules.md).
- **pw-core[__EXTRAS__]** (`pw_core.*`) — the web app factory and error pages, db and
  migrations, LLM clients and output guards, jobs, fetch, the Typer CLI scaffold. Module index:
  [docs/pw-core/modules.md](docs/pw-core/modules.md) (`web.md` and `database.md` first).

`pw-setup` links each package's bundled docs under `docs/`; if a link is missing, run
`bin/run pw-setup`. Use the framework's logging, config, exceptions, db and web layers rather
than re-implementing them.
