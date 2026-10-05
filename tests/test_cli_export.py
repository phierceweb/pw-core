"""Tests for pf_core.cli.export — the ``pf-export`` CLI and its mountable sub-app."""

from __future__ import annotations

import threading
import tomllib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from typer.testing import CliRunner

from pf_core.cli.export import app, main
from pf_core.db import transaction
from pf_core.jobs._schema import jobs
from pf_core.llm.tracking import LlmRunRepo, metadata
from pf_core.llm.tracking import otel as otel_mod

runner = CliRunner()

_OTEL_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_SERVICE_NAME",
    "OTEL_RESOURCE_ATTRIBUTES",
)


@pytest.fixture()
def tracking_db(pf_engine):
    metadata.create_all(pf_engine)
    yield pf_engine
    metadata.drop_all(pf_engine)


@pytest.fixture(autouse=True)
def _otel_env(monkeypatch):
    for var in _OTEL_VARS:
        # setenv first so delenv records the var and teardown undoes a .env load.
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture()
def captured(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(otel_mod, "_otlp_exporter", lambda: exporter)
    return exporter


class _Collector:
    def __init__(self, status: int) -> None:
        self.status = status
        self.requests: list[tuple[str, dict[str, str], bytes]] = []


@pytest.fixture()
def collector() -> Iterator[tuple[str, _Collector]]:
    yield from _serve(200)


@pytest.fixture()
def rejecting_collector() -> Iterator[tuple[str, _Collector]]:
    yield from _serve(400)


def _serve(status: int) -> Iterator[tuple[str, _Collector]]:
    state = _Collector(status)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state.requests.append((self.path, dict(self.headers), body))
            self.send_response(state.status)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()


def _run(**kwargs) -> int:
    kwargs.setdefault("agent_type", "drafter")
    kwargs.setdefault("model", "anthropic/claude-sonnet-4.6")
    return LlmRunRepo().record(**kwargs)


def _pf_export(*args: str) -> int:
    try:
        main(list(args))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_pf_export_is_a_declared_console_script():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts["pf-export"] == "pf_core.cli.export:main"


def test_mounted_app_exposes_otel_as_a_subcommand():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "otel" in result.output


def test_dry_run_reports_counts_and_sends_nothing(tracking_db, captured):
    _run()
    _run(agent_type="reviewer")

    result = runner.invoke(app, ["otel", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "2 span(s) in 2 trace(s)" in result.output
    assert captured.get_finished_spans() == ()


def test_options_select_runs_and_bodies(tracking_db, captured):
    keep = _run(rendered_prompts=(None, "hi"), raw_response="hello")
    _run(agent_type="reviewer", rendered_prompts=(None, "hi"), raw_response="hello")
    _run(agent_type="judge", rendered_prompts=(None, "hi"), raw_response="hello")

    result = runner.invoke(
        app,
        [
            "otel",
            "--since",
            "2000-01-01",
            "--until",
            "2999-01-01T00:00:00+00:00",
            "--agent-type",
            "drafter",
            "--include-bodies",
            "judge,drafter",
        ],
    )

    assert result.exit_code == 0, result.output
    (span,) = captured.get_finished_spans()
    assert span.attributes["pf_core.run_id"] == keep
    assert "gen_ai.output.messages" in span.attributes


def test_job_id_option_filters(tracking_db, captured):
    with transaction() as conn:
        job = int(
            conn.execute(
                jobs.insert().values(kind="demo", status="succeeded")
            ).inserted_primary_key[0]
        )
    in_job = _run(job_id=job)
    _run()

    result = runner.invoke(app, ["otel", "--job-id", str(job)])

    assert result.exit_code == 0, result.output
    assert [s.attributes["pf_core.run_id"] for s in captured.get_finished_spans()] == [in_job]


def test_unparseable_since_is_a_usage_error(tracking_db, captured):
    assert _pf_export("otel", "--since", "last tuesday") == 2
    assert captured.get_finished_spans() == ()


def test_console_script_posts_otlp_to_the_endpoint_named_in_dotenv(
    tracking_db, collector, tmp_path, monkeypatch
):
    url, state = collector
    (tmp_path / ".env").write_text(f"OTEL_EXPORTER_OTLP_ENDPOINT={url}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer%20t0k")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "cli-test")
    run_ids = {_run(), _run()}

    assert _pf_export("otel") == 0

    ((path, headers, body),) = state.requests
    assert path == "/v1/traces"
    assert headers["authorization"] == "Bearer t0k"
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    (resource_spans,) = request.resource_spans
    service = {a.key: a.value.string_value for a in resource_spans.resource.attributes}
    assert service["service.name"] == "cli-test"
    exported = {
        a.value.int_value
        for scope in resource_spans.scope_spans
        for span in scope.spans
        for a in span.attributes
        if a.key == "pf_core.run_id"
    }
    assert exported == run_ids


def test_a_rejected_export_exits_1(tracking_db, rejecting_collector, monkeypatch):
    url, state = rejecting_collector
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", url)
    _run()

    assert _pf_export("otel") == 1
    assert len(state.requests) == 1
