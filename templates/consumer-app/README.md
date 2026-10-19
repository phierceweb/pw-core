# __NAME__

A full-stack app (FastAPI + SQLAlchemy, app-layout) on pw-foundation and pw-core.

## Setup

```bash
bin/setup        # venv (3.12+) + editable install + .env + framework wiring
bin/web          # start the server → http://127.0.0.1:8000/
```

## Commands

```bash
bin/web          # run the server (uvicorn)
bin/test         # pytest
bin/lint         # ruff + the pw-guards structural gate (file sizes + layering)
```

## Layout

`app/` holds the application: `api/` (routes only), and — as the app grows —
`services/` (business logic), `repo/` (data access), `orchestrators/`
(multi-step workflows). No layer imports from a layer above it; see
`.ai/rules/layering.md` and `.ai/rules/project-structure.md`. Built on
[pw-foundation](https://github.com/phierceweb/pw-foundation) and
[pw-core](https://github.com/phierceweb/pw-core).
