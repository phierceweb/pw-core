"""
Eval harness config — YAML schema and loader.

YAML format (``config/eval.yaml``)::

    imports: [app.eval_comparators]  # imported on load: register comparators, load settings

    defaults:
      compare: structured_diff
      pass_threshold: 0.85
      parallelism: 4
      sampling:
        temperature: 0.0

    agents:
      drafter:
        compare: llm_judge
        judge_agent_type: drafter_judge
        pass_threshold: 0.80
      classifier:
        compare: structured_diff
        diff_fields: [category, confidence]
        tolerances:
          confidence: 0.10
        pass_threshold: 0.95

Environment variable:

- ``EVAL_CONFIG`` — path to eval.yaml. Default: ``config/eval.yaml``.

See ``docs/eval-harness.md`` for the full reference.
"""

from __future__ import annotations

import importlib
import os
import threading
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator
except ImportError as e:  # pragma: no cover - exercised by bare-install CI
    from pf_core._extras import extra_import_error

    raise extra_import_error("eval", "pydantic", feature="pf_core.eval") from e

from pf_core.exceptions import ConfigurationError

_DEFAULT_PATH = "config/eval.yaml"
_TOP_LEVEL_KEYS = ("imports", "defaults", "agents")

_lock = threading.Lock()
_cache: dict[str, "EvalConfig"] = {}


class MetricGate(BaseModel):
    """Hard gate on a named metric stored in ``llm_run_metrics``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    min: float | None = None
    max: float | None = None


class AgentEvalConfig(BaseModel):
    """Per-agent eval policy. Loaded from ``agents.<slug>`` block in eval.yaml."""

    model_config = ConfigDict(extra="forbid")

    compare: str = "structured_diff"
    pass_threshold: float = 0.85
    parallelism: int = 4
    sampling: dict[str, Any] = Field(default_factory=lambda: {"temperature": 0.0})
    diff_fields: list[str] = Field(default_factory=list)
    tolerances: dict[str, float] = Field(default_factory=dict)
    judge_agent_type: str | None = None
    metrics: list[MetricGate] = Field(default_factory=list)

    @field_validator("sampling")
    @classmethod
    def _sampling_leaves_routing_alone(cls, value: dict[str, Any]) -> dict[str, Any]:
        """``model``/``backend`` here would silently beat the run's target."""
        routed = sorted({"model", "backend"} & set(value))
        if routed:
            raise ValueError(
                f"sampling may not set {', '.join(routed)}; model_router.yaml or the target does"
            )
        return value


def _section(data: dict[str, Any], key: str, *, where: str | None = None) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(
            f"eval config '{where or key}' must be a mapping, got {type(value).__name__}"
        )
    return value


def _agent_config(where: str, values: dict[str, Any]) -> AgentEvalConfig:
    try:
        return AgentEvalConfig(**values)
    except (TypeError, ValueError) as exc:  # pydantic's ValidationError is a ValueError
        raise ConfigurationError(f"eval config '{where}' is invalid: {exc}") from exc


class EvalConfig:
    """Container for a parsed eval.yaml file.

    Args:
        data: Raw YAML-decoded dict.

    Raises:
        ConfigurationError: If a section, the defaults, or any agent block is invalid,
            or an ``agents`` key is not a string.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        unknown = sorted(str(k) for k in data if k not in _TOP_LEVEL_KEYS)
        if unknown:
            raise ConfigurationError(
                f"eval config has unknown key(s) {', '.join(repr(k) for k in unknown)} — "
                f"expected {', '.join(_TOP_LEVEL_KEYS)}"
            )
        self._defaults_raw: dict[str, Any] = _section(data, "defaults")
        self._agents_raw: dict[str, Any] = _section(data, "agents")
        unquoted = [k for k in self._agents_raw if not isinstance(k, str)]
        if unquoted:
            raise ConfigurationError(
                f"eval config agents key(s) {', '.join(repr(k) for k in unquoted)} read as "
                "non-strings (YAML); quote them"
            )
        self.defaults: AgentEvalConfig = _agent_config("defaults", self._defaults_raw)
        for agent_type in self._agents_raw:
            self.for_agent(agent_type)

    def for_agent(self, agent_type: str) -> AgentEvalConfig:
        """Return merged config for an agent (defaults + agent-specific overrides).

        Agent-level keys win over defaults. With no ``agents`` entries every agent gets the
        defaults; otherwise the agent must be listed, ``<agent_type>: {}`` for the defaults alone.

        Raises:
            ConfigurationError: If ``agents`` lists other agents but not this one, or the
                merged block is invalid.
        """
        merged = {**self._defaults_raw}
        if agent_type in self._agents_raw:
            merged.update(_section(self._agents_raw, agent_type, where=f"agents.{agent_type}"))
        elif self._agents_raw:
            listed = ", ".join(sorted(str(k) for k in self._agents_raw))
            raise ConfigurationError(
                f"eval config has no agents.{agent_type} block (it lists {listed}); "
                f"add '{agent_type}: {{}}' to run it on the defaults"
            )
        return _agent_config(f"agents.{agent_type}", merged)

    def __repr__(self) -> str:
        return f"EvalConfig(agents={sorted(self._agents_raw)}, defaults={self._defaults_raw})"


def load_eval_config(path: str | None = None) -> EvalConfig:
    """Load eval.yaml and import the modules its ``imports`` lists, caching by path.

    Args:
        path: Override path. Falls back to ``EVAL_CONFIG`` env var, then
            ``config/eval.yaml``. Only that default may be missing, which
            gives an empty config: every agent on the framework defaults.

    Raises:
        ConfigurationError: If ``path`` is empty, a path given by ``path`` or
            ``EVAL_CONFIG`` does not exist, or the file cannot be parsed, is not
            a valid eval config, or lists a module that fails to import.
    """
    if path is not None and not path.strip():
        raise ConfigurationError("eval config path is empty")
    named = path or os.environ.get("EVAL_CONFIG")
    config_path = named or _DEFAULT_PATH
    with _lock:
        if config_path in _cache:
            return _cache[config_path]
    p = Path(config_path)
    if not p.exists():
        if named:
            source = "" if path else " (from EVAL_CONFIG)"
            raise ConfigurationError(f"eval config {config_path!r}{source} does not exist")
        # Uncached, so a later call that names this path still finds it missing.
        return EvalConfig({})
    try:
        from pf_core.utils.yaml import safe_load

        data = safe_load(p.read_text()) or {}
    except Exception as exc:
        raise ConfigurationError(f"Cannot parse eval config {config_path!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"eval config {config_path!r} must be a mapping, got {type(data).__name__}"
        )
    # Outside the lock: an imported module may itself call load_eval_config.
    _import_project_modules(data.get("imports"), config_path)
    cfg = EvalConfig(data)
    with _lock:
        return _cache.setdefault(config_path, cfg)


def _import_project_modules(modules: Any, config_path: str) -> None:
    """Import the modules eval.yaml lists under ``imports`` (comparators, settings)."""
    if modules is None:
        return
    if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
        raise ConfigurationError(
            f"eval config {config_path!r}: 'imports' must be a list of module names, "
            f"got {modules!r}"
        )
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            raise ConfigurationError(
                f"eval config {config_path!r}: cannot import {name!r} from 'imports': {exc}"
            ) from exc


def clear_config_cache() -> None:
    """Clear the in-memory config cache (useful in tests)."""
    with _lock:
        _cache.clear()
