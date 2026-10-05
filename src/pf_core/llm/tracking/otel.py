"""
Export ``llm_runs`` to an OpenTelemetry backend as GenAI client spans.

One span per run. The runs of a job share one trace; a run without a job is a
trace of its own. Span and trace ids are derived from the run and job ids
(scoped by the resource's ``service.name``), so exporting the same rows again
sends the same spans. The OTLP/HTTP endpoint, headers and service name come
from the standard ``OTEL_*`` environment variables.

Usage::

    from pf_core.llm.tracking.otel import export_runs

    summary = export_runs(since=datetime(2026, 10, 1), include_bodies=["summarizer"])

The attribute mapping and the pinned semantic-conventions version are in
``docs/otel.md``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Collection
from dataclasses import asdict, dataclass
from typing import Any

try:
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags
except ImportError as e:  # pragma: no cover - exercised by bare-install CI
    from pf_core._extras import extra_import_error

    raise extra_import_error(
        "otel", "opentelemetry-sdk", feature="pf_core.llm.tracking.otel"
    ) from e

from pf_core.exceptions import ClientError, InvalidInputError
from pf_core.llm.tracking._otel_reader import RunReader
from pf_core.log import get_logger
from pf_core.utils.env import resolve_int

logger = get_logger(__name__)

SEMCONV_SCHEMA_URL = "https://opentelemetry.io/schemas/1.41.0"
_SCOPE = InstrumentationScope("pf_core.llm.tracking.otel", schema_url=SEMCONV_SCHEMA_URL)

_BATCH_SIZE_ENV_VAR = "OTEL_BSP_MAX_EXPORT_BATCH_SIZE"
_BATCH_SIZE_DEFAULT = 512

# Statuses pf-core itself writes for a call that did not fail.
_OK_STATUSES = frozenset({"success", "cache_hit"})
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_BODY_KEYS = frozenset({"gen_ai.input.messages", "gen_ai.output.messages"})


@dataclass(frozen=True)
class OtelExportSummary:
    """What one :func:`export_runs` call sent (or, on a dry run, would send)."""

    spans: int
    traces: int
    with_bodies: int


def export_runs(
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    agent_type: str | None = None,
    job_id: int | None = None,
    include_bodies: Collection[str] = (),
    dry_run: bool = False,
    exporter: SpanExporter | None = None,
    resource: Resource | None = None,
) -> OtelExportSummary:
    """Send the selected ``llm_runs`` rows to an OpenTelemetry exporter, oldest id first.

    Args:
        since: Only runs created at or after this time. Naive values are UTC.
        until: Only runs created before this time. Naive values are UTC.
        agent_type: Only runs of this agent type slug.
        job_id: Only runs attributed to this job.
        include_bodies: Agent type slugs whose rendered prompts and raw response
            are exported as ``gen_ai.input.messages`` / ``gen_ai.output.messages``.
            Every other run is exported without its text.
        dry_run: Build the spans and count them; send nothing.
        exporter: Where spans go. Default: an OTLP/HTTP exporter configured from
            the ``OTEL_EXPORTER_OTLP_*`` environment variables, shut down on return.
        resource: The resource attached to every span. Default:
            ``Resource.create()``, which reads ``OTEL_SERVICE_NAME`` and
            ``OTEL_RESOURCE_ATTRIBUTES``.

    Returns:
        Counts of spans, distinct traces, and spans that carry bodies.

    Raises:
        InvalidInputError: ``since`` is not before ``until``.
        ClientError: The exporter rejected a batch. Earlier batches stay sent;
            re-running resends them under the same ids.
    """
    since, until = _utc_naive(since), _utc_naive(until)
    if since is not None and until is not None and since >= until:
        raise InvalidInputError(
            f"since ({since.isoformat()}) must be before until ({until.isoformat()})"
        )
    resource = resource or Resource.create()
    service = str(resource.attributes.get(SERVICE_NAME, ""))
    owned = exporter is None and not dry_run
    sink = _otlp_exporter() if owned else exporter

    sent = with_bodies = 0
    traces: set[int] = set()
    try:
        for page in RunReader().pages(
            size=_batch_size(),
            since=since,
            until=until,
            agent_type=agent_type,
            job_id=job_id,
            bodies_for=frozenset(include_bodies),
        ):
            batch = [_span(row, tags, payload, resource, service) for row, tags, payload in page]
            if sink is not None and not dry_run:
                _send(sink, batch, already_sent=sent)
            sent += len(batch)
            traces.update(span.context.trace_id for span in batch if span.context)
            with_bodies += len(
                [sp for sp in batch if not _BODY_KEYS.isdisjoint(sp.attributes or {})]
            )
    finally:
        if owned and sink is not None:
            sink.shutdown()

    summary = OtelExportSummary(spans=sent, traces=len(traces), with_bodies=with_bodies)
    logger.info("otel_export_finished", dry_run=dry_run, **asdict(summary))
    return summary


def _otlp_exporter() -> SpanExporter:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError as e:
        from pf_core._extras import extra_import_error

        raise extra_import_error(
            "otel", "opentelemetry-exporter-otlp-proto-http", feature="pf-export otel"
        ) from e
    return OTLPSpanExporter()


def _batch_size() -> int:
    n = resolve_int(None, _BATCH_SIZE_ENV_VAR, default=_BATCH_SIZE_DEFAULT)
    return n if n > 0 else _BATCH_SIZE_DEFAULT


def _send(exporter: SpanExporter, batch: list[ReadableSpan], *, already_sent: int) -> None:
    if exporter.export(batch) is not SpanExportResult.SUCCESS:
        raise ClientError(
            "the OpenTelemetry exporter rejected a span batch; its log has the reason",
            context={"batch_spans": len(batch), "already_sent": already_sent},
        )


def _utc_naive(value: dt.datetime | None) -> dt.datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _span(
    row: dict[str, Any],
    tags: frozenset[str],
    payload: dict[str, Any] | None,
    resource: Resource,
    service: str,
) -> ReadableSpan:
    run_id = int(row["id"])
    trace_key = f"run:{run_id}" if row["job_id"] is None else f"job:{int(row['job_id'])}"
    truncated = "truncated" in tags
    attributes = _attributes(row, tags, truncated=truncated)
    if payload is not None:
        attributes.update(_bodies(payload, finish_reason=_finish_reason(row, truncated=truncated)))
    end = _ns(row["created_at"])
    duration_ms = max(row["duration_ms"] or 0, 0)
    # Built directly, not through a Tracer: a backfill needs the stored times and derived ids,
    # and a Tracer's processors log a rejected batch instead of raising.
    return ReadableSpan(
        name=f"chat {row['model']}",
        context=SpanContext(
            trace_id=_derive_id(service, trace_key, 16),
            span_id=_derive_id(service, f"span:{run_id}", 8),
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        ),
        parent=None,
        resource=resource,
        attributes=attributes,
        kind=SpanKind.CLIENT,
        status=_status(row),
        start_time=end - duration_ms * 1_000_000,
        end_time=end,
        instrumentation_scope=_SCOPE,
    )


def _attributes(row: dict[str, Any], tags: frozenset[str], *, truncated: bool) -> dict[str, Any]:
    status = row["status"]
    cost = row["cost_usd"]
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": row["provider"],
        # Superseded by gen_ai.provider.name in the pinned semconv; some backends still read it.
        "gen_ai.system": row["provider"],
        "gen_ai.request.model": row["model"],
        "gen_ai.request.temperature": row["temperature"],
        "gen_ai.request.top_p": row["top_p"],
        "gen_ai.request.max_tokens": row["max_tokens"],
        "gen_ai.request.seed": row["seed"],
        "gen_ai.request.stop_sequences": _strings(row["stop_sequences"]),
        "gen_ai.usage.input_tokens": _input_tokens(row),
        "gen_ai.usage.output_tokens": row["completion_tokens"],
        "gen_ai.usage.cache_read.input_tokens": row["cache_read_tokens"],
        "gen_ai.usage.cache_creation.input_tokens": row["cache_write_tokens"],
        "gen_ai.usage.reasoning.output_tokens": row["reasoning_tokens"],
        "gen_ai.response.finish_reasons": ("length",) if truncated else None,
        "pf_core.run_id": int(row["id"]),
        "pf_core.agent_type": row["agent_type"],
        "pf_core.job_id": row["job_id"],
        "pf_core.prompt.system_id": row["system_prompt_id"],
        "pf_core.prompt.user_id": row["user_prompt_id"],
        "pf_core.cost_usd": float(cost) if cost is not None else None,
        "pf_core.cache_hit": status == "cache_hit" or any(t.startswith("cache:") for t in tags),
        "pf_core.status": status,
    }
    if status not in _OK_STATUSES:
        attrs["error.type"] = row["error_class"] or status
        attrs["pf_core.error_code"] = row["error_code"]
        attrs["http.response.status_code"] = row["http_status"]
    return {key: value for key, value in attrs.items() if value is not None}


def _input_tokens(row: dict[str, Any]) -> int | None:
    """The semconv total: pf-core's ``prompt_tokens`` excludes cache reads and writes."""
    if row["prompt_tokens"] is None:
        return None
    return int(
        row["prompt_tokens"] + (row["cache_read_tokens"] or 0) + (row["cache_write_tokens"] or 0)
    )


def _strings(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return tuple(value)
    return None


def _status(row: dict[str, Any]) -> Status:
    if row["status"] in _OK_STATUSES:
        return Status(StatusCode.UNSET)
    return Status(StatusCode.ERROR, description=row["error"] or None)


def _finish_reason(row: dict[str, Any], *, truncated: bool) -> str:
    """Required on an output message; no column stores it, so derive it from tags and status."""
    if truncated:
        return "length"
    return "stop" if row["status"] in _OK_STATUSES else "error"


def _bodies(payload: dict[str, Any], *, finish_reason: str) -> dict[str, str]:
    out: dict[str, str] = {}
    inputs = [
        {"role": role, "parts": [{"type": "text", "content": text}]}
        for role, text in (
            ("system", payload["rendered_system"]),
            ("user", payload["rendered_user"]),
        )
        if text is not None
    ]
    if inputs:
        out["gen_ai.input.messages"] = json.dumps(inputs, ensure_ascii=False)
    if payload["raw_response"] is not None:
        message = {
            "role": "assistant",
            "parts": [{"type": "text", "content": payload["raw_response"]}],
            "finish_reason": finish_reason,
        }
        out["gen_ai.output.messages"] = json.dumps([message], ensure_ascii=False)
    return out


def _derive_id(service: str, key: str, size: int) -> int:
    digest = hashlib.sha256(f"pf_core\x00{service}\x00{key}".encode()).digest()
    return int.from_bytes(digest[:size], "big") or 1  # 0 is OTel's invalid id


def _ns(when: dt.datetime) -> int:
    aware = when.replace(tzinfo=dt.timezone.utc) if when.tzinfo is None else when
    return (aware - _EPOCH) // dt.timedelta(microseconds=1) * 1000


__all__ = ["SEMCONV_SCHEMA_URL", "OtelExportSummary", "export_runs"]
