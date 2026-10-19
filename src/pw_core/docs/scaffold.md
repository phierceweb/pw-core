# New-consumer scaffold

`bin/new-consumer` stamps a new pw-core consumer project from a template — a runnable, conformant skeleton, so a project adopts the framework's conventions from day one instead of rebuilding into them later.

It's a **from-checkout** dev tool: the generator reads `templates/` from the pw-core source tree (these aren't shipped in the wheel). The generated project then depends on pw-core however it likes — a git tag now, a PyPI version later.

## Usage

```bash
bin/new-consumer <name> --layout {lib|app} [--extras cli,llm] [--base-only] [--dest DIR]
```

- **`lib`** — a CLI / library in the src-layout (`src/<pkg>/`), no web or DB. Default extras: `cli`.
- **`app`** — a full-stack FastAPI + SQLAlchemy app (`app/` layout). Default extras: `full`.

```bash
bin/new-consumer my-tool --layout lib
cd ./my-tool        # created under --dest (default: cwd)
bin/setup
bin/run pw-doctor   # attest the install
bin/run hello       # the day-1 slice
```

Every scaffold declares `pw-foundation~=1.0` beside the `pw-core[...]` pin, because a pw-core
consumer imports `pw_foundation` directly. `--base-only` (lib layout only) scaffolds a project
on pw-foundation alone: `pw-foundation~=1.0` is the sole dependency, `cli.py` is argparse on
`pw_foundation.log`, nothing in the tree names pw-core, and no rules are copied from the
checkout — `bin/setup` runs `pw-setup`, which installs pw-foundation's starter rules.

## What it generates

- `pyproject.toml` — the pw-core pin + chosen extras, a shared ruff config, build-system, and (lib) a console-script entry.
- `.pw-guards.toml` — the structural-gate config (correct scan root per layout).
- `bin/{setup,run,lint,test}` (plus `bin/web` for `app`) — wrappers over the project venv. `bin/setup` installs a sibling `../pw-foundation` or `../pw-core` checkout editable when one exists (co-development, or a release not yet on PyPI) before resolving the pins, then runs `pw-setup`.
- **Enforcement wired, not just documented**: `.pre-commit-config.yaml` (structural gate + ruff on every commit; `bin/setup` installs the hooks when the project is a git repo) and a `.github/workflows/guards.yml` CI backstop.
- The layout skeleton + a **day-1 vertical slice** that runs immediately: `bin/run hello` (lib) or a `GET /` route (app).
- `.ai/` stubs; `bin/setup` runs `pw-setup`, which installs the starter rules (pw-foundation's seven and pw-core's three), links both packages' docs at `docs/pw-foundation/` and `docs/pw-core/`, and points `.claude/` and `.cursor/` at `.ai/`
- `tests/` with a smoke test, plus `.env.example`, `.gitignore`, and a `CLAUDE.md` that names what each package provides and where its docs are linked.

A template file with a base-only twin — `cli_base.py`, `config_base.py`, `test_smoke_base.py`, `CLAUDE.base.md`, `README.base.md` — replaces its sibling under `--base-only`, so a foundation-only project says only what is true of pw-foundation. The pw-core pin is the version of the checkout running `bin/new-consumer`.

## Conventions baked in

The generated project follows the `project-structure` rule (in the repo's `.ai/rules/`) for its layout, is linted by `pw_foundation.guards` via `bin/lint` (reading the stamped `.pw-guards.toml` — `root = "src/<pkg>"` for lib, `root = "app"` for app), and pins pw-core from PyPI at the current compatible line (see the template `pyproject.toml`).
