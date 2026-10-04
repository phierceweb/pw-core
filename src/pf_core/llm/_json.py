"""JSON-column hygiene for parsed LLM output."""

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
