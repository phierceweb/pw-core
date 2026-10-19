# __NAME__

A library / tool (src-layout — no web or database layers) on pw-foundation and pw-core.

## Setup

```bash
bin/setup          # venv (3.12+) + editable install + .env + framework wiring
bin/run pw-doctor  # attest the install
bin/run hello      # the day-1 vertical slice — replace it with your own commands
```

## Commands

```bash
bin/run <cmd>    # the __NAME__ CLI (src/__PKG__/cli.py)
bin/test         # pytest
bin/lint         # ruff + the pw-guards structural gate
```

## Layout

`src/__PKG__/` is the importable package. Add a domain package under it as the
code grows; introduce `<domain>/services/`, `orchestrators/`, or `utils/`
directories only once each holds two or more files (see
`.ai/rules/project-structure.md`). Built on
[pw-foundation](https://github.com/phierceweb/pw-foundation) and
[pw-core](https://github.com/phierceweb/pw-core).
