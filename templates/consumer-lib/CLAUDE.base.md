# __NAME__ — AI assistant context

A library/tool in the **src-layout** (no web/DB layers), built on pw-foundation.

## Rules

Project rules live in `.ai/rules/`, installed by `pw-setup` (which `bin/setup` runs):
pw-foundation's code style, config-driven design, error handling, logging, framework-first,
docs-sync and plans rules. `.claude/rules` and `.cursor/rules` point at them.

## Layout

- `src/__PKG__/cli.py` — CLI entry (thin; argparse on `pw_foundation.log`).
- `src/__PKG__/config.py` — `Config(AppConfig)` subclass of `pw_foundation.config.AppConfig`; the `cfg` singleton.
- Add one package per domain under `src/__PKG__/`. Grow a layer dir
  (`<domain>/services/`, `orchestrators/`, `utils/`) only when it has ≥2 files.

## Commands

`bin/setup`, `bin/run <cmd>`, `bin/test`, `bin/lint`.

## The framework

**pw-foundation** (`pw_foundation.*`), declared in `pyproject.toml` as `pw-foundation~=1.0` —
logging, exceptions, the config base and env resolvers, pure utils (dates, ids, hashing, json,
yaml, slugify, throttle), `Service`, parallel helpers, output reporters, and the `pw-guards` /
`pw-doctor` / `pw-setup` tools. Module index:
[docs/pw-foundation/modules.md](docs/pw-foundation/modules.md).

`pw-setup` links the bundled docs at `docs/pw-foundation/`; if the link is missing, run
`bin/run pw-setup`. Never reach for a third-party library when pw-foundation already provides it.
