"""Split modules keep every name they exposed: importable, and visible to ``mypy --strict``."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

_IN_PLACE: dict[str, tuple[str, ...]] = {
    "pw_core.clients.anthropic": (
        "AnthropicClient",
        "DEFAULT_MAX_TOKENS",
        "DEFAULT_PREFLIGHT_TIMEOUT_SECONDS",
        "DEFAULT_TIMEOUT_SECONDS",
        "get_client",
        "new_client",
        "reset_client",
    ),
    "pw_core.budget.check": ("CostBudgetExceeded", "check_budget", "logger"),
    "pw_core.eval._runner": ("EvalRunner", "logger"),
}

# module -> {name it exposed before the split: module that defines it now}
_MOVED: dict[str, dict[str, str]] = {
    "pw_core.clients.anthropic": {
        "AnthropicError": "pw_core.clients._anthropic_errors",
        "_JSON_OBJECT_INSTRUCTION": "pw_core.clients._anthropic_request",
        "_apply_response_format": "pw_core.clients._anthropic_request",
        "_response_format_warned": "pw_core.clients._anthropic_request",
        "_split_system": "pw_core.clients._anthropic_request",
        "_RETRY_BACKOFF_BASE": "pw_core.clients._anthropic_retry",
        "_RETRYABLE_STATUS_CODES": "pw_core.clients._anthropic_retry",
        "_is_retryable": "pw_core.clients._anthropic_retry",
        "_timeout_left_the_client": "pw_core.clients._anthropic_retry",
    },
    "pw_core.budget.check": {
        "compute_period_end": "pw_core.budget.periods",
        "compute_period_start": "pw_core.budget.periods",
        "current_spent": "pw_core.budget.periods",
        "current_usage": "pw_core.budget.periods",
        "project_cost": "pw_core.budget.projection",
        "_enforcement_disabled": "pw_core.budget.config",
    },
    "pw_core.eval._runner": {
        "_apply_metric_gates": "pw_core.eval._scoring",
        "_judge_slug": "pw_core.eval._scoring",
    },
}


def _load(module: str, name: str) -> object:
    return getattr(importlib.import_module(module), name)


@pytest.mark.parametrize(
    ("module", "name"),
    [pytest.param(m, n, id=f"{m}.{n}") for m, names in _IN_PLACE.items() for n in names],
)
def test_a_name_the_module_defines_still_imports(module: str, name: str) -> None:
    assert _load(module, name) is not None


@pytest.mark.parametrize(
    ("module", "name", "home"),
    [pytest.param(m, n, h, id=f"{m}.{n}") for m, names in _MOVED.items() for n, h in names.items()],
)
def test_a_moved_name_is_the_object_in_its_new_home(module: str, name: str, home: str) -> None:
    assert _load(module, name) is _load(home, name)


def test_a_strict_consumer_imports_every_name_without_error(tmp_path: Path, monkeypatch) -> None:
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is not installed")
    names = {m: [*_IN_PLACE.get(m, ()), *_MOVED.get(m, {})] for m in {*_IN_PLACE, *_MOVED}}
    consumer = tmp_path / "consumer.py"
    consumer.write_text(
        "".join(f"from {m} import {', '.join(ns)}\n" for m, ns in sorted(names.items()))
    )
    config = tmp_path / "mypy.ini"
    config.write_text("[mypy]\n")
    monkeypatch.setenv("MYPYPATH", str(SRC))
    recursion_limit = sys.getrecursionlimit()  # mypy.api.run raises it for the whole process
    try:
        out, err, status = mypy_api.run(
            [
                "--strict",
                "--follow-imports=silent",  # report the consumer only, as for an installed pw_core
                "--config-file",
                str(config),
                "--cache-dir",
                str(tmp_path / "mypy-cache"),
                str(consumer),
            ]
        )
    finally:
        sys.setrecursionlimit(recursion_limit)
    assert status == 0, out + err
