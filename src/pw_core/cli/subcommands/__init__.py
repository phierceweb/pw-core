"""Typer subcommand factories for `pw_core.pipeline.*` operations.

Wraps `pw_core.pipeline.{baseline, baseline_diff, cache}` so consumers
that build a Typer CLI on top of those modules don't have to write the
same boilerplate Typer command bodies for every pipeline-style project.

Two factories:

  - :func:`make_invalidate_subcommand` — registers a single CLI command
    that calls :func:`pw_core.pipeline.cache.invalidate_caches`.
  - :func:`make_baseline_subcommand_group` — registers a Typer
    subcommand group with `save` / `list` / `diff` subcommands that
    call :func:`pw_core.pipeline.baseline.save_baseline`,
    :func:`pw_core.pipeline.baseline.list_baselines`, and
    :func:`pw_core.pipeline.baseline_diff.diff_baseline` respectively.

Project-specific bindings
(filenames, dir names) are parameterized via
:class:`pw_core.pipeline.baseline.BaselineConfig` and the
`run_record_filename` keyword on the invalidate factory.
"""

from __future__ import annotations

from .baseline import make_baseline_subcommand_group
from .invalidate import make_invalidate_subcommand

__all__ = [
    "make_baseline_subcommand_group",
    "make_invalidate_subcommand",
]
