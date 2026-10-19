# pw-core — Shared Python Framework

## What this is

`pw-core` is a shared framework package that provides the foundational layers for Python/OpenRouter web applications. It is installed as a dependency by individual projects.

## Consumer projects

pw-core is designed to be consumed by multiple projects — web apps, batch pipelines, and CLIs. When something proves useful in two consumers, it gets absorbed into pw-core. When something only makes sense in one, it stays out (see [`.ai/rules/scope.md`](.ai/rules/scope.md)).

## Architecture

Four-layer architecture: **Repository** → **Service** → **Orchestrator** → entry points (CLI/Web).

## Rules

All project rules live in `.claude/rules/` (symlinked to `.ai/rules/`). Read those files for coding standards, layering constraints, error handling patterns, and other project conventions.

## Plans

Implementation plans and tracking documents live in `.claude/plans/` (symlinked to `.ai/plans/`).

## Skills

Project-specific skills live in `.claude/skills/` (symlinked to `.ai/skills/`). Shared skills are in `~/.claude/skills/`.

## Docs

Extended documentation for each module lives in `docs/`. Written for AI assistants first; see the `docs` skill for authoring guidance.

## How projects use this

The foundation layer (logging, exceptions, the config base, utils, `Service` base, the `pw-guards` build gate, `pw-doctor`, `pw-setup`) is the separate `pw-foundation` package — a hard dependency that consumers import directly as `pw_foundation.*`; pw-core never re-exports it. LLM clients and anti-slop guards live behind the `[llm]` extra; capabilities compose orthogonally (`[db]` without LLM, `[web]` without `[db]`, `[llm]` standalone). See [`docs/INSTALLATION.md`](docs/INSTALLATION.md).

```bash
pip install -e ../pw-core                  # editable install — base only (pw-foundation comes with it)
pip install pw-core[llm]                   # LLM clients + anti-slop guards (httpx, json-repair, pydantic)
pip install pw-core[redis]                 # with optional Redis support
pip install pw-core[articles]              # trafilatura + htmldate for article_fetch
pip install pw-core[dev]                   # with pytest fixtures
```

A bare `pip install pw-core` no longer ships the LLM stack. Importing `pw_core.clients.*` / `pw_core.llm.*` without `[llm]` (or `pw_core.utils.urls` without `[http]`) raises a friendly `ImportError` naming the extra.

## Testing

```bash
pip install -e ".[dev]"
pytest
```
