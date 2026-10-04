# YAML Utilities

`pf_core.utils.yaml` loads YAML the way `yaml.safe_load` does, except that a repeated mapping key raises instead of silently replacing the earlier value.

This is about parsing only. To locate a config file use the [config-path resolver](config-path.md); for settings resolution (override > env > YAML > default) see [config](config.md).

---

## Table of Contents

- [Why](#why)
- [Usage](#usage)
- [What counts as a repeat](#what-counts-as-a-repeat)
- [Errors](#errors)
- [pf-core's own config files](#pf-cores-own-config-files)
- [Adding a new config loader](#adding-a-new-config-loader)

---

## Why

PyYAML builds each mapping as a dict, so a second `summarizer:` block in `model_router.yaml` or a second scope in `budgets.yaml` replaces the first with no error. For configuration that is a silent misconfiguration: the agent runs on the wrong model, or a spend cap disappears.

## Usage

```python
from pf_core.utils.yaml import UniqueKeyLoader, safe_load

data = safe_load(path.read_text(encoding="utf-8"))  # drop-in for yaml.safe_load

import yaml

data = yaml.load(text, Loader=UniqueKeyLoader)  # when you need the Loader class
```

`safe_load` accepts what `yaml.safe_load` accepts (a string, bytes, or an open file) and returns the same result for any document without a repeated key.

## What counts as a repeat

- Keys compare as the values they construct to, the way the resulting dict would: `1:` and `true:` repeat each other, as do `null:` and `~:`.
- A key brought in by a `<<` merge may be overridden by the mapping's own key — that is how merges are meant to be used.
- Only scalar keys are compared. A sequence or mapping used as a key is left to PyYAML's own handling.

## Errors

A repeat raises `yaml.constructor.ConstructorError`, a `yaml.YAMLError`, naming the key and the line and column of both the mapping and the repeat. Code that already catches `yaml.YAMLError` handles it without changes.

Do: catch `yaml.YAMLError` at the loader and raise `ConfigurationError` that names the file.
Do not: catch it and continue with an empty config unless that loader's documented contract is to never raise.

## pf-core's own config files

Every YAML config pf-core reads goes through `safe_load`:

- `AppConfig`'s YAML tier, `model_router.yaml`, prompt files, `eval.yaml`, and `budgets.yaml` raise `ConfigurationError`.
- `cache.yaml` keeps its never-raise contract: it logs `cache_config_load_failed` and turns caching off, as it does for any file that fails to parse ([llm-cache](llm-cache.md)).

## Adding a new config loader

1. Read the file with `pf_core.utils.yaml.safe_load`, not `yaml.safe_load`.
2. Wrap `yaml.YAMLError` in `ConfigurationError` with the file path in the message.
3. Test it with a file that repeats a key: the loader should raise, not load the last value.
