"""Non-finite float hygiene for run sidecars: JSON columns and float metrics."""

from __future__ import annotations

import math
from typing import Any


def finite_json(value: Any) -> Any:
    """*value* with NaN and ±Infinity replaced by ``None``: Postgres and MySQL reject them in JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    return value


def is_non_finite(value: Any) -> bool:
    """``True`` for a float NaN or ±Infinity, which a nullable float column must store as null."""
    return isinstance(value, float) and not math.isfinite(value)


def split_finite(metrics: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """``(name → float for the finite values, names of the non-finite ones)``."""
    finite: dict[str, float] = {}
    dropped: list[str] = []
    for name, value in metrics.items():
        number = float(value)
        if math.isfinite(number):
            finite[name] = number
        else:
            dropped.append(name)
    return finite, dropped
