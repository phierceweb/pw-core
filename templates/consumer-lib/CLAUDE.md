# __NAME__ — AI assistant context

A library/tool in the **src-layout** (no web/DB layers), built on pw-foundation and pw-core.

## Rules

Project rules live in `.ai/rules/`, installed by `pw-setup` (which `bin/setup` runs) from both
packages: pw-foundation's (code style, config-driven design, error handling, logging,
framework-first, docs-sync, plans) and pw-core's (layering, anti-patterns, project-structure).
`.claude/rules` and `.cursor/rules` point at them.

## Layout

- `src/__PKG__/cli.py` — CLI entry (thin; `pw_core.cli.create_cli` / `run_cli`).
- `src/__PKG__/config.py` — `Config(AppConfig)` subclass of `pw_core.config.AppConfig`; the `cfg` singleton.
- Add one package per domain under `src/__PKG__/`. Grow a layer dir
  (`<domain>/services/`, `orchestrators/`, `utils/`) only when it has ≥2 files.

## Commands

`bin/setup`, `bin/run <cmd>`, `bin/test`, `bin/lint`.

## The framework

Two packages, both declared in `pyproject.toml`:

- **pw-foundation** (`pw_foundation.*`) — logging, exceptions, the config base and env
  resolvers, pure utils (dates, ids, hashing, json, yaml, slugify, throttle), `Service`,
  parallel helpers, output reporters, and the `pw-guards` / `pw-doctor` / `pw-setup` tools.
  Module index: [docs/pw-foundation/modules.md](docs/pw-foundation/modules.md).
- **pw-core[__EXTRAS__]** (`pw_core.*`) — the Typer CLI scaffold, LLM clients and output
  guards, fetch, db, web, jobs. Module index: [docs/pw-core/modules.md](docs/pw-core/modules.md).

`pw-setup` links each package's bundled docs under `docs/`; if a link is missing, run
`bin/run pw-setup`. Never reach for a third-party library when the framework already provides it.
