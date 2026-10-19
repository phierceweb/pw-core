"""Tests for pw_core.llm.tracking.otel — llm_runs rows exported as OTel GenAI spans."""

from __future__ import annotations

import calendar
import datetime as dt
import json

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from pw_core.db import transaction
from pw_foundation.exceptions import ClientError, InvalidInputError
from pw_core.jobs._schema import jobs
from pw_core.llm.tracking import (
    LlmRunRepo,
    metadata,
    resolve_agent_type_id,
    resolve_prompt_id,
)
from pw_core.llm.tracking.otel import OtelExportSummary, export_runs

MODEL = "anthropic/claude-sonnet-4.6"
T_END = dt.datetime(2026, 10, 1, 12, 0, 0, 250000)


@pytest.fixture()
def tracking_db(pw_engine):
    metadata.create_all(pw_engine)
    yield pw_engine
    metadata.drop_all(pw_engine)


def _job(**values) -> int:
    with transaction() as conn:
        res = conn.execute(jobs.insert().values(kind="demo", status="succeeded", **values))
        return int(res.inserted_primary_key[0])


def _run(*, created_at: dt.datetime = T_END, **kwargs) -> int:
    kwargs.setdefault("agent_type", "drafter")
    kwargs.setdefault("model", MODEL)
    return LlmRunRepo().record(extra_run_values={"created_at": created_at}, **kwargs)


def _export(**kwargs) -> tuple[OtelExportSummary, dict]:
    exporter = InMemorySpanExporter()
    summary = export_runs(exporter=exporter, **kwargs)
    spans = {int(s.attributes["pw_core.run_id"]): s for s in exporter.get_finished_spans()}
    return summary, spans


def _ns(when: dt.datetime) -> int:
    return calendar.timegm(when.timetuple()) * 10**9 + when.microsecond * 1000


def _ids(spans: dict) -> dict[int, tuple[int, int]]:
    return {rid: (s.context.trace_id, s.context.span_id) for rid, s in spans.items()}


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_span_attributes_map_every_run_column(tracking_db):
    agent_id = resolve_agent_type_id("drafter")
    sys_id = resolve_prompt_id(agent_type_id=agent_id, part="system", version=1, content="S")
    user_id = resolve_prompt_id(agent_type_id=agent_id, part="user", version=2, content="U")
    job = _job()
    run_id = _run(
        system_prompt_id=sys_id,
        user_prompt_id=user_id,
        sampling={
            "temperature": 0.25,
            "top_p": 0.5,
            "max_tokens": 512,
            "seed": 7,
            "stop_sequences": ["END"],
        },
        provider="openrouter",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "cache_read_tokens": 900,
            "cache_write_tokens": 50,
            "reasoning_tokens": 10,
            "cost_usd": 0.0123,
            "duration_ms": 1500,
        },
        job_id=job,
    )

    _, spans = _export()

    assert dict(spans[run_id].attributes) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openrouter",
        "gen_ai.system": "openrouter",
        "gen_ai.request.model": MODEL,
        "gen_ai.request.temperature": 0.25,
        "gen_ai.request.top_p": 0.5,
        "gen_ai.request.max_tokens": 512,
        "gen_ai.request.seed": 7,
        "gen_ai.request.stop_sequences": ("END",),
        # The semconv total: uncached prompt_tokens plus both cache columns.
        "gen_ai.usage.input_tokens": 1050,
        "gen_ai.usage.output_tokens": 40,
        "gen_ai.usage.cache_read.input_tokens": 900,
        "gen_ai.usage.cache_creation.input_tokens": 50,
        "gen_ai.usage.reasoning.output_tokens": 10,
        "pw_core.run_id": run_id,
        "pw_core.agent_type": "drafter",
        "pw_core.job_id": job,
        "pw_core.prompt.system_id": sys_id,
        "pw_core.prompt.user_id": user_id,
        "pw_core.cost_usd": 0.0123,
        "pw_core.cache_hit": False,
        "pw_core.status": "success",
    }


def test_minimal_run_emits_only_known_values(tracking_db):
    run_id = _run()

    _, spans = _export()

    assert dict(spans[run_id].attributes) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": MODEL,
        "pw_core.run_id": run_id,
        "pw_core.agent_type": "drafter",
        "pw_core.cache_hit": False,
        "pw_core.status": "success",
    }


def test_span_is_a_genai_client_span_named_for_the_model(tracking_db):
    run_id = _run()

    _, spans = _export()

    span = spans[run_id]
    assert span.name == f"chat {MODEL}"
    assert span.kind is SpanKind.CLIENT
    assert span.instrumentation_scope.name == "pw_core.llm.tracking.otel"
    assert span.instrumentation_scope.schema_url == "https://opentelemetry.io/schemas/1.41.0"


def test_span_ends_at_created_at_and_starts_duration_earlier(tracking_db):
    timed = _run(usage={"duration_ms": 1500})
    untimed = _run()

    _, spans = _export()

    assert spans[timed].end_time == _ns(T_END)
    assert spans[timed].start_time == _ns(T_END) - 1_500_000_000
    assert spans[untimed].start_time == spans[untimed].end_time == _ns(T_END)


# ---------------------------------------------------------------------------
# Trace grouping and ids
# ---------------------------------------------------------------------------


def test_runs_of_one_job_share_a_trace_and_jobless_runs_get_their_own(tracking_db):
    job_a, job_b = _job(), _job()
    a1, a2 = _run(job_id=job_a), _run(job_id=job_a)
    b1 = _run(job_id=job_b)
    solo1, solo2 = _run(), _run()

    summary, spans = _export()

    assert spans[a1].context.trace_id == spans[a2].context.trace_id
    assert len({spans[r].context.trace_id for r in (a1, b1, solo1, solo2)}) == 4
    assert len({s.context.span_id for s in spans.values()}) == 5
    assert all(s.parent is None for s in spans.values())
    assert summary == OtelExportSummary(spans=5, traces=4, with_bodies=0)


def test_a_job_and_a_jobless_run_with_the_same_number_get_different_traces(tracking_db):
    solo = _run()
    job = _job(id=solo)
    in_job = _run(job_id=job)

    _, spans = _export()

    assert job == solo
    assert spans[solo].context.trace_id != spans[in_job].context.trace_id


def test_reexport_produces_the_same_ids(tracking_db):
    job = _job()
    _run(job_id=job)
    _run(job_id=job)
    _run()

    _, first = _export()
    _, second = _export()

    assert len(first) == 3
    assert _ids(first) == _ids(second)
    assert all(trace and span for trace, span in _ids(first).values())


def test_ids_are_scoped_by_service_name(tracking_db):
    run_id = _run()

    _, a = _export(resource=Resource.create({"service.name": "project-a"}))
    _, b = _export(resource=Resource.create({"service.name": "project-b"}))

    assert a[run_id].context.trace_id != b[run_id].context.trace_id
    assert a[run_id].context.span_id != b[run_id].context.span_id
    assert a[run_id].resource.attributes["service.name"] == "project-a"


# ---------------------------------------------------------------------------
# Tags, status
# ---------------------------------------------------------------------------


def test_truncated_run_reports_finish_reason_length(tracking_db):
    cut = _run(usage={"finish_reason": "length", "completion_tokens": 512})
    whole = _run(usage={"finish_reason": "stop", "completion_tokens": 20})

    _, spans = _export()

    assert spans[cut].attributes["gen_ai.response.finish_reasons"] == ("length",)
    assert "gen_ai.response.finish_reasons" not in spans[whole].attributes


def test_cache_hit_run_is_flagged_and_not_an_error(tracking_db):
    hit = _run(
        status="cache_hit",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "duration_ms": 2},
        tags=["cache:exact", "cache_age:fresh"],
    )
    ok = _run()

    _, spans = _export()

    assert spans[hit].attributes["pw_core.cache_hit"] is True
    assert spans[hit].status.status_code is StatusCode.UNSET
    assert spans[ok].attributes["pw_core.cache_hit"] is False
    assert spans[ok].status.status_code is StatusCode.UNSET


def test_a_cache_tag_alone_flags_a_cache_hit(tracking_db):
    tagged = _run(tags=["cache:semantic"])
    aged = _run(tags=["cache_age:fresh"])

    _, spans = _export()

    assert spans[tagged].attributes["pw_core.cache_hit"] is True
    assert spans[aged].attributes["pw_core.cache_hit"] is False


def test_a_missing_otlp_exporter_names_the_otel_extra(monkeypatch):
    import sys

    from pw_core.llm.tracking import otel

    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    with pytest.raises(ImportError, match=r"pip install pw-core\[otel\]"):
        otel._otlp_exporter()


def test_failed_run_is_an_error_span_with_its_error_fields(tracking_db):
    run_id = _run(
        status="failed",
        error="rate limited upstream",
        error_class="ClientError",
        error_code="rate_limited",
        http_status=429,
    )

    _, spans = _export()

    span = spans[run_id]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "rate limited upstream"
    assert span.attributes["error.type"] == "ClientError"
    assert span.attributes["pw_core.error_code"] == "rate_limited"
    assert span.attributes["http.response.status_code"] == 429
    assert span.attributes["pw_core.status"] == "failed"


def test_non_success_status_without_error_class_types_the_error_by_status(tracking_db):
    run_id = _run(status="budget_blocked")

    _, spans = _export()

    span = spans[run_id]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "budget_blocked"
    assert "pw_core.error_code" not in span.attributes
    assert "http.response.status_code" not in span.attributes


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------

_BODY_KEYS = {"gen_ai.input.messages", "gen_ai.output.messages"}


def _with_payload(agent_type: str, **kwargs) -> int:
    return _run(
        agent_type=agent_type,
        rendered_prompts=("Be terse.", "Summarize: café"),
        raw_response='{"summary": "ok"}',
        **kwargs,
    )


def test_bodies_are_not_exported_by_default(tracking_db):
    run_id = _with_payload("drafter")

    summary, spans = _export()

    assert not _BODY_KEYS & set(spans[run_id].attributes)
    assert summary.with_bodies == 0


def test_include_bodies_exports_messages_for_named_agents_only(tracking_db):
    drafter = _with_payload("drafter")
    reviewer = _with_payload("reviewer")

    summary, spans = _export(include_bodies=["drafter"])

    attrs = spans[drafter].attributes
    assert json.loads(attrs["gen_ai.input.messages"]) == [
        {"role": "system", "parts": [{"type": "text", "content": "Be terse."}]},
        {"role": "user", "parts": [{"type": "text", "content": "Summarize: café"}]},
    ]
    assert json.loads(attrs["gen_ai.output.messages"]) == [
        {
            "role": "assistant",
            "parts": [{"type": "text", "content": '{"summary": "ok"}'}],
            "finish_reason": "stop",
        }
    ]
    assert not _BODY_KEYS & set(spans[reviewer].attributes)
    assert summary.with_bodies == 1


def test_truncated_output_message_carries_finish_reason(tracking_db):
    run_id = _with_payload("drafter", usage={"finish_reason": "length"})

    _, spans = _export(include_bodies=["drafter"])

    (message,) = json.loads(spans[run_id].attributes["gen_ai.output.messages"])
    assert message["finish_reason"] == "length"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({}, "stop"),
        ({"usage": {"finish_reason": "length"}}, "length"),
        ({"status": "cache_hit"}, "stop"),
        ({"status": "failed", "error": "boom"}, "error"),
    ],
)
def test_every_output_message_has_the_semconv_required_keys(tracking_db, kwargs, reason):
    run_id = _with_payload("drafter", **kwargs)

    _, spans = _export(include_bodies=["drafter"])

    (message,) = json.loads(spans[run_id].attributes["gen_ai.output.messages"])
    assert {"role", "parts", "finish_reason"} <= set(message)
    assert message["finish_reason"] == reason


def test_included_agent_without_payload_gets_no_body_attributes(tracking_db):
    run_id = _run()

    summary, spans = _export(include_bodies=["drafter"])

    assert not _BODY_KEYS & set(spans[run_id].attributes)
    assert summary.with_bodies == 0


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_since_until_select_a_half_open_window(tracking_db):
    day = dt.timedelta(days=1)
    before = _run(created_at=T_END - day)
    inside = _run(created_at=T_END)
    at_until = _run(created_at=T_END + day)

    _, spans = _export(since=T_END, until=T_END + day)

    assert set(spans) == {inside}
    assert before not in spans and at_until not in spans


def test_aware_bounds_are_compared_in_utc(tracking_db):
    inside = _run(created_at=T_END)
    plus_two = dt.timezone(dt.timedelta(hours=2))

    _, spans = _export(since=T_END.replace(tzinfo=dt.timezone.utc).astimezone(plus_two))

    assert set(spans) == {inside}


def test_agent_type_and_job_id_filter_the_runs(tracking_db):
    job = _job()
    drafted = _run(agent_type="drafter", job_id=job)
    reviewed = _run(agent_type="reviewer", job_id=job)
    _run(agent_type="reviewer")

    _, by_agent = _export(agent_type="drafter")
    _, by_job = _export(job_id=job)
    _, by_both = _export(agent_type="reviewer", job_id=job)

    assert set(by_agent) == {drafted}
    assert set(by_job) == {drafted, reviewed}
    assert set(by_both) == {reviewed}


def test_since_after_until_is_invalid_input(tracking_db):
    with pytest.raises(InvalidInputError):
        export_runs(exporter=InMemorySpanExporter(), since=T_END, until=T_END)


# ---------------------------------------------------------------------------
# Export loop
# ---------------------------------------------------------------------------


class _CountingExporter(InMemorySpanExporter):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[int] = []

    def export(self, spans):
        self.batches.append(len(spans))
        return super().export(spans)


class _FailingExporter(InMemorySpanExporter):
    def export(self, spans):
        return SpanExportResult.FAILURE


def test_batch_size_follows_the_standard_otel_env_var(tracking_db, monkeypatch):
    monkeypatch.setenv("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "2")
    run_ids = {_run() for _ in range(5)}
    exporter = _CountingExporter()

    summary = export_runs(exporter=exporter)

    assert exporter.batches == [2, 2, 1]
    assert {int(s.attributes["pw_core.run_id"]) for s in exporter.get_finished_spans()} == run_ids
    assert summary.spans == 5


def test_dry_run_counts_without_exporting(tracking_db):
    job = _job()
    _run(job_id=job)
    _run(job_id=job)
    _run()
    exporter = InMemorySpanExporter()

    summary = export_runs(exporter=exporter, dry_run=True)

    assert exporter.get_finished_spans() == ()
    assert summary == OtelExportSummary(spans=3, traces=2, with_bodies=0)


def test_a_rejected_batch_raises_client_error(tracking_db):
    _run()

    with pytest.raises(ClientError):
        export_runs(exporter=_FailingExporter())


def test_no_matching_runs_exports_nothing(tracking_db):
    exporter = _CountingExporter()

    summary = export_runs(exporter=exporter, agent_type="nobody")

    assert exporter.batches == []
    assert summary == OtelExportSummary(spans=0, traces=0, with_bodies=0)
