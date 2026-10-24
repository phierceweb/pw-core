"""Anthropic request shaping: system-prompt extraction and response_format mapping."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pw_core.clients._anthropic_errors import AnthropicError

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

_JSON_OBJECT_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else — no prose, no code fences."
)
# One-shot warning guard, keyed by response_format type.
_response_format_warned: set[str] = set()


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Extract leading system-role messages into a single system string.

    Anthropic takes the system prompt as a top-level ``system=`` request
    param — a ``role: "system"`` entry inside ``messages`` is rejected by
    the API. Only leading system messages with string content are
    extracted; everything from the first non-system (or non-string-content)
    message on is returned untouched.
    """
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if msg.get("role") == "system" and isinstance(content, str):
            parts.append(content)
        else:
            break
    if not parts:
        return None, messages
    return "\n\n".join(parts), messages[len(parts) :]


def _apply_response_format(
    response_format: dict | None, call_kwargs: dict, system: str | None, *, log: BoundLogger
) -> str | None:
    """Map an OpenAI-style ``response_format`` onto Anthropic request kwargs.

    ``json_schema`` uses native structured outputs (``output_config.format``);
    accepts both the bare ``{"type": "json_schema", "schema": {...}}`` shape
    and the OpenAI nesting ``{"type": "json_schema", "json_schema":
    {"schema": {...}}}``. ``json_object`` has no native equivalent (Anthropic
    schemas require ``additionalProperties: false``, so a permissive object
    schema is inexpressible) — enforced by appending a JSON-only instruction
    to the system string, logged once per process. Unknown types warn once
    and are ignored so clients stay drop-in swappable. Mutates
    ``call_kwargs``; returns the (possibly amended) system string.
    """
    if not response_format:
        return system
    fmt_type = response_format.get("type")
    if fmt_type == "json_schema":
        schema = response_format.get("schema")
        if schema is None:
            schema = (response_format.get("json_schema") or {}).get("schema")
        if schema is None:
            raise AnthropicError(
                "response_format type 'json_schema' requires a schema — pass "
                "{'type': 'json_schema', 'schema': {...}} or the OpenAI "
                "nesting {'type': 'json_schema', 'json_schema': "
                "{'schema': {...}}}."
            )
        call_kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        return system
    if fmt_type == "json_object":
        if "json_object" not in _response_format_warned:
            _response_format_warned.add("json_object")
            log.warning(
                "anthropic_json_object_prompt_enforced",
                detail="no native json_object mode; enforcing via system instruction",
            )
        if system:
            return f"{system}\n\n{_JSON_OBJECT_INSTRUCTION}"
        return _JSON_OBJECT_INSTRUCTION
    key = f"unknown:{fmt_type}"
    if key not in _response_format_warned:
        _response_format_warned.add(key)
        log.warning("anthropic_response_format_ignored", format_type=str(fmt_type))
    return system
