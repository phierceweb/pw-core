# Pricing

Shared LLM cost estimation. One home for per-model rates, used by the clients to populate `usage["cost_usd"]` and callable directly for a pre-call budget estimate. Foundation-tier — pure-Python, no dependencies, imports on the base install.

## Usage

```python
from pw_core.pricing import estimate_cost

cost = estimate_cost(
    "anthropic",
    "claude-opus-4-7",
    prompt_tokens=51_096,
    completion_tokens=2_628,
)
```

`estimate_cost(provider, model, *, prompt_tokens=0, completion_tokens=0, cache_read_tokens=0, cache_write_tokens=0, cache_ttl="5m") -> float` returns USD. Rates are per 1,000,000 tokens.

## Resolution

- **Prefix match.** The model id is matched against the provider's table by prefix; the first match (insertion order) wins, so specific prefixes come first: `claude-opus-4-7` resolves to its own entry, `claude-opus-4-1-20250805` to `claude-opus-4`. A prefix matches the id as written or with its dots read as dashes, so the dotted `claude-opus-4.5` matches `claude-opus-4-5`.
- **Namespaced ids.** An OpenRouter-style `anthropic/claude-opus-4-7` is split on `/` and priced against the underlying provider's table — so `estimate_cost("openrouter", "anthropic/claude-...")` works.
- **Provider with no table of its own** — including an empty or unrecognised `provider` — is searched against every table instead. A provider that *has* a table, including the deliberately empty `openai` and `google`, is never widened.
- **Unknown model → `0.0`**, with a one-shot `pricing_unknown_model` log warning per `provider:model` per process. Treat `0.0` as "unpriced", not "free".
- **Cache tokens** are added only when the model's `ModelRates` defines `cache_read` / `cache_write`; the built-in Anthropic entries define both. `cache_ttl="1h"` selects `cache_write_1h` when defined, falling back to `cache_write`; any other value uses `cache_write`.
- **Long prompts.** A model with `long_prompt` rates prices a call whose prompt — `prompt_tokens` plus cache reads and writes — is over `long_prompt_threshold` tokens entirely at those rates, as Anthropic bills Claude Haiku 5.5.

## Built-in rates

The `anthropic` table ships Anthropic's first-party API rates for the Claude 5.x, 4.x and legacy 3.x families, cache rates included; batch discounts, fast-mode rates and the `inference_geo` surcharge are not modeled. The `openai` and `google` tables ship **empty on purpose** — a stale price is worse than a visible `0.0`. These are a baseline, not a maintained price feed; verify against each provider's pricing page.

## Adding or correcting rates

Register at startup, without editing the framework:

```python
from pw_core.pricing import ModelRates, register_rates

register_rates("openai", "gpt-4o-mini", ModelRates(input=0.15, output=0.60))
```

Registered rates override a built-in with the same prefix. `ModelRates(input, output, cache_read=None, cache_write=None, cache_write_1h=None, long_prompt_threshold=None, long_prompt=None)` — rates per 1M tokens; `cache_write` is the 5-minute-TTL write rate, `cache_write_1h` the 1-hour one; `long_prompt` is a `ModelRates` with no tier of its own, set together with a positive `int` `long_prompt_threshold`, and defining every cache rate the base rates define. A dotted prefix matches only dotted ids; register the dashed spelling to cover both. `get_rates(provider, model)` returns the resolved `ModelRates` (or `None`) for inspection.

## Client integration

`AnthropicClient.chat()` populates `usage["cost_usd"]` via `estimate_cost`. `OpenRouterClient.chat()` prefers OpenRouter's server-reported `usage.cost` and falls back to `estimate_cost` only when a route doesn't surface one. The Claude Code client reports `0.0` (a Claude Max session has no per-call price). See [cost & budget](cost-budget.md) for using estimates in pre-call budget gating.

## Other public names

From `pw_core.pricing`:

- `PricingError` — raised by `register_rates` for a missing provider or model prefix, or rates that are not a `ModelRates`, and by `ModelRates` for a long-prompt tier that breaks those rules.
