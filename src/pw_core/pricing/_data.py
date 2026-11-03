"""Built-in per-model pricing tables, USD per 1,000,000 tokens.

Keyed by model-id **prefix**; the resolver takes the first match in
insertion order, so list more-specific prefixes first. Anthropic entries
are the first-party API rates, cache reads and writes included; batch
discounts, fast-mode rates and the ``inference_geo`` surcharge are not modeled.

These are a convenience baseline, not a maintained price feed. Verify
against each provider's pricing page and add entries as models ship — or,
without editing the framework, register them at startup with
:func:`pw_core.pricing.register_rates`.

The ``openai`` and ``google`` tables ship empty deliberately: shipping a
stale price is worse than returning ``0.0`` (which is visible and warns).
"""

from __future__ import annotations

from pw_core.pricing._types import ModelRates


def _rates(input: float, output: float, cache_read: float) -> ModelRates:
    """Rates whose cache writes are Anthropic's multiples of the input rate."""
    return ModelRates(
        input=input,
        output=output,
        cache_read=cache_read,
        cache_write=input * 1.25,
        cache_write_1h=input * 2,
    )


_FABLE_5_1 = _rates(10.0, 50.0, 0.25)
_FABLE_5 = _rates(10.0, 50.0, 1.0)
_OPUS_4_5_UP = _rates(5.0, 25.0, 0.5)

ANTHROPIC: dict[str, ModelRates] = {
    "claude-fable-5-1": _FABLE_5_1,
    "claude-mythos-5-1": _FABLE_5_1,
    "claude-fable-5": _FABLE_5,
    "claude-mythos-5": _FABLE_5,
    "claude-opus-5-5": _rates(4.0, 20.0, 0.2),
    "claude-opus-5": _OPUS_4_5_UP,
    "claude-opus-4-8": _OPUS_4_5_UP,
    "claude-opus-4-7": _OPUS_4_5_UP,
    "claude-opus-4-6": _OPUS_4_5_UP,
    "claude-opus-4-5": _OPUS_4_5_UP,
    "claude-opus-4": _rates(15.0, 75.0, 1.5),
    "claude-sonnet-5-5": _rates(2.0, 10.0, 0.1),
    "claude-sonnet-5": _rates(2.0, 10.0, 0.2),
    "claude-sonnet-4": _rates(3.0, 15.0, 0.3),
    "claude-haiku-5-5": ModelRates(
        input=0.1,
        output=0.5,
        cache_read=0.01,
        cache_write=0.125,
        cache_write_1h=0.2,
        long_prompt_threshold=100_000,
        long_prompt=_rates(0.5, 2.5, 0.05),
    ),
    "claude-haiku-4": _rates(1.0, 5.0, 0.1),
    "claude-3-opus": _rates(15.0, 75.0, 1.5),
    "claude-3-5-sonnet": _rates(3.0, 15.0, 0.3),
    "claude-3-5-haiku": _rates(0.8, 4.0, 0.08),
    "claude-3-haiku": _rates(0.25, 1.25, 0.025),
}

OPENAI: dict[str, ModelRates] = {}

GOOGLE: dict[str, ModelRates] = {}
