"""Types for pw_core.pricing."""

from __future__ import annotations

from dataclasses import dataclass


class PricingError(Exception):
    """Invalid pricing-table registration (bad provider, model, or rates)."""


@dataclass(frozen=True)
class ModelRates:
    """USD rates per 1,000,000 tokens for one model (or model-id prefix).

    ``cache_read`` / ``cache_write`` are optional — left ``None`` they are
    not modeled, and cached tokens add nothing to the estimate (the
    conservative default that matches a plain input+output calculation).
    ``cache_write`` is the default-TTL (5-minute) write rate;
    ``cache_write_1h`` is the 1-hour-TTL write rate, used when the caller
    passes ``cache_ttl="1h"`` to :func:`estimate_cost` (falls back to
    ``cache_write`` when unset).

    ``long_prompt`` prices, in full, a call whose prompt — uncached input plus
    cache reads and writes — is over ``long_prompt_threshold`` tokens. The two
    are set together.

    Raises:
        PricingError: if only one of ``long_prompt`` / ``long_prompt_threshold``
            is set, the threshold is not a positive ``int``, or ``long_prompt`` is
            not a ``ModelRates``, has a tier of its own, or leaves out a cache rate
            these rates define.
    """

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    cache_write_1h: float | None = None
    long_prompt_threshold: int | None = None
    long_prompt: ModelRates | None = None

    def __post_init__(self) -> None:
        tier, threshold = self.long_prompt, self.long_prompt_threshold
        if (tier is None) != (threshold is None):
            raise PricingError("long_prompt and long_prompt_threshold are set together")
        if tier is None:
            return
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0:
            raise PricingError("long_prompt_threshold must be a positive int")
        if not isinstance(tier, ModelRates) or tier.long_prompt is not None:
            raise PricingError("long_prompt must be a ModelRates with no tier of its own")
        for name in ("cache_read", "cache_write", "cache_write_1h"):
            if getattr(self, name) is not None and getattr(tier, name) is None:
                raise PricingError(f"long_prompt must define {name}, as these rates do")
