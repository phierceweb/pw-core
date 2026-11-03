# Contributing to pw-core

Thanks for your interest. pw-core is a dependency-light Python foundation with
opt-in extras; contributions that keep it lean, well-tested, and documented are
welcome.

## Scope — read this first

pw-core is **infrastructure only**. It must never contain business logic, domain
models, or project-specific configuration — anything that only makes sense
inside one application belongs in that application, not here. It does ship
generic entry points (the mountable admin routers, the `pw-*` console scripts);
those are fine because everything they touch is framework-owned.

A feature earns its place in pw-core when **two or more independent projects**
need it. A pattern that lives in a single project should stay there until a
second consumer wants it. See [`.ai/rules/scope.md`](.ai/rules/scope.md).

## Development setup

Python 3.12+ is required.

```bash
git clone https://github.com/phierceweb/pw-core
cd pw-core
python -m venv .venv && source .venv/bin/activate
# Everything, so the full test suite runs:
pip install -e ".[full,otel,articles,anthropic,image-phash,dev]"
pre-commit install
```

For lighter work, install only the extras you're touching — the base
`pip install -e ".[dev]"` is enough for foundation-only changes. See
[`docs/INSTALLATION.md`](src/pw_core/docs/INSTALLATION.md) for the extras matrix.

## Before you open a pull request

These checks run in CI and as pre-commit hooks — run them locally first:

```bash
bin/test                                # full suite, must be green
bin/run ruff check src tests            # lint
bin/run ruff format --check src tests   # formatting (apply with `ruff format src tests`)
bin/run python -m mypy                  # type check (config in [tool.mypy])
bin/run python -m pw_foundation.guards        # structural gate (reads .pw-guards.toml)
```

`bin/test` and `bin/run` dispatch through `.venv/`. `bin/test` is the same
wrapper `bin/new-consumer` scaffolds into consumer projects; `bin/run` here runs
any `.venv/bin` executable, while the scaffolded one starts the project's own CLI
or app. With the venv activated the bare `pytest` / `ruff` commands work too.

And hold the change to these standards:

- **Tests travel with code.** New behavior needs tests; a bug fix needs a
  regression test that fails before your change and passes after.
- **Docs travel with code.** A change to a module's public API is incomplete
  without the matching `docs/*.md` update — see
  [`.ai/rules/docs-sync.md`](.ai/rules/docs-sync.md).
- **File-size gate.** Python files over the hard limit fail the build; over the
  soft target they warn (canonical values in `pw_foundation/guards/config.py`).
  Split by concern instead of growing a monolith — see
  [`.ai/rules/code-style.md`](.ai/rules/code-style.md).
- **Layering.** Respect the layered architecture (repository → service →
  orchestrator → entry point); no layer imports from a layer above it. See
  [`.ai/rules/layering.md`](.ai/rules/layering.md). The layering check reads
  layers from a consumer's `app/<layer>/` tree, so it does not run on pw-core
  itself; here the enforced contract is import tiers —
  `tests/test_extras_tiers.py` and the `bare` CI job keep each extra importable
  without the others.

## Coding conventions

The full set lives in [`.ai/rules/`](.ai/rules/). The essentials:

- Modern Python 3.12+ syntax — `X | None`, lowercase `dict`/`list`/`tuple`.
- Type hints on every public signature; Google-style docstrings on public APIs.
- Structured logging via `pw_foundation.log.get_logger(__name__)` — never bare `print`
  outside CLI entry points.
- Raise from the `pw_foundation.exceptions` hierarchy — never a bare `Exception`.

## Versioning & deprecations

pw-core follows [semantic versioning](https://semver.org): a patch release is fixes only, a minor release is additive and backward-compatible, and a major release may break. Pin to a release (`pw-core~=1.0`), not `main`, which carries unreleased work between tags.

The stable surface is what the docs indexed in [`docs/modules.md`](src/pw_core/docs/modules.md) show: those modules and the names they document, imported from the paths they show. Anything else — an undocumented submodule, a `_`-prefixed module or name, a doc marked **Experimental**, `pw_core.guards_rules`, the admin dashboard's `api`, `pages` and `queries` modules apart from `queries.job_detail` — may change in any release. `tests/test_public_surface.py` checks that an indexed page names every public module or a package above it, and that every exported name — a module's `__all__`, else its lazy re-exports, else its public top-level defs and constants — appears on an indexed page that also shows its module.

A deprecated API keeps working and emits a `DeprecationWarning` naming its replacement until the next major release.

## License

Contributions are accepted under the Apache License 2.0. Opening a pull request
licenses your work to the project under those terms.

## Questions

Open an issue for bugs and feature requests. For anything security-sensitive,
follow [`SECURITY.md`](SECURITY.md) instead of filing a public issue.
