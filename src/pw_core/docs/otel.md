# OpenTelemetry Export

Send tracked LLM runs (`llm_runs`) to any OTLP/HTTP backend — Phoenix, Langfuse, Jaeger, an OpenTelemetry Collector — as GenAI client spans, with `pw-export otel` or `pw_core.llm.tracking.otel.export_runs`.

This is a **backfill from the database**, not live instrumentation: pw-core emits no spans while calls run. It reads the same rows the [LLM admin dashboard](llm-admin.md) reads ([llm-tracking.md](llm-tracking.md) owns the schema). It is unrelated to structured logging ([logging.md](https://github.com/phierceweb/pw-foundation/blob/main/src/pw_foundation/docs/logging.md)).

---

## Table of Contents

- [Install](#install)
- [Run an export](#run-an-export)
- [Configure the endpoint](#configure-the-endpoint)
- [Trace shape, timing and ids](#trace-shape-timing-and-ids)
- [Attribute mapping](#attribute-mapping)
- [Prompt and response bodies](#prompt-and-response-bodies)
- [Failures and partial exports](#failures-and-partial-exports)
- [From Python](#from-python)
- [Mounting in a project CLI](#mounting-in-a-project-cli)
- [Adding a new attribute](#adding-a-new-attribute)

---

## Install

```bash
pip install "pw-core[otel]"   # = [tracking,cli] + opentelemetry-api/sdk + the OTLP/HTTP exporter
```

`[full]` does not include it: export is opt-in tooling.

---

## Run an export

```bash
pw-export otel                                   # every run
pw-export otel --since 2026-10-01 --until 2026-10-02T00:00:00+00:00
pw-export otel --agent-type summarizer --job-id 42
pw-export otel --include-bodies summarizer,reviewer
pw-export otel --dry-run                         # count spans and traces; send nothing
```

- `--since` / `--until` take ISO 8601 dates or date-times and select the half-open window `[since, until)` on `created_at`. A value without an offset is UTC.
- `--include-bodies` takes comma-separated agent types (repeatable); see [bodies](#prompt-and-response-bodies).
- Runs go out oldest id first. The command prints how many spans, traces, and spans with bodies it sent.

`pw-export` loads `./.env` before it runs (shell variables win), so it picks up the project's `DATABASE_URL` and `OTEL_*` settings the way `pw-eval` does.

---

## Configure the endpoint

Configure the export with the standard OpenTelemetry environment variables; pw-core adds none of its own.

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:6006            # /v1/traces is appended
OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer%20<token>    # URL-encoded key=value pairs
OTEL_SERVICE_NAME=my-project                                 # also scopes the span ids
```

`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `_TRACES_HEADERS`, `_TIMEOUT`, `_COMPRESSION`, the certificate variables and `OTEL_RESOURCE_ATTRIBUTES` work as the OpenTelemetry SDK documents them. `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` (default 512) sets how many spans go in one request.

**Do:** set `OTEL_SERVICE_NAME` per project before the first export and keep it. **Do not** change it between exports of the same database — see [ids](#trace-shape-timing-and-ids).

---

## Trace shape, timing and ids

- **One span per `llm_runs` row**, kind `CLIENT`, named `chat {model}` (the GenAI span-name rule).
- **One trace per job.** Every run with the same `job_id` shares a trace; a run without a job is a trace of its own. Spans have no parent: the job itself gets no span, and `llm_run_links` (retry, critic, cache) are not turned into parent/child edges.
- **Timing:** the span ends at `created_at` (the row is written when the call returns) and starts `duration_ms` earlier. A row without a duration is a zero-length span.
- **Deterministic ids:** the trace id is a hash of `service.name` and the job id (or, for a jobless run, the run id — the two never collide); the span id is a hash of `service.name` and the run id. Exporting the same rows again sends identical ids, so a re-run after a failure, or an overlapping `--since` window, produces the same spans rather than new ones. Whether a backend then stores one copy or two is the backend's behavior: Phoenix keys spans by span id and keeps one.

Two projects that export to one backend with the same `service.name` would produce colliding ids for equal job and run numbers — another reason to set `OTEL_SERVICE_NAME`.

---

## Attribute mapping

Names follow the **OpenTelemetry GenAI semantic conventions as published in semantic-conventions v1.41.0** — the last core release before they moved to the `semantic-conventions-genai` repository (the `opentelemetry-semantic-conventions` 0.66b0 Python package carries the same names). They are experimental (Development stability). The instrumentation scope `pw_core.llm.tracking.otel` carries `schema_url` `https://opentelemetry.io/schemas/1.41.0`.

An attribute is omitted when its column is NULL.

| Attribute | From |
|---|---|
| `gen_ai.operation.name` | always `chat` |
| `gen_ai.provider.name`, `gen_ai.system` | `provider` (`gen_ai.system` is the superseded name, kept for backends that still read it) |
| `gen_ai.request.model` | `llm_models.name` |
| `gen_ai.request.temperature` / `top_p` / `max_tokens` / `seed` / `stop_sequences` | the sampling columns |
| `gen_ai.usage.input_tokens` | `prompt_tokens + cache_read_tokens + cache_write_tokens` |
| `gen_ai.usage.cache_read.input_tokens` / `cache_creation.input_tokens` | `cache_read_tokens` / `cache_write_tokens` |
| `gen_ai.usage.output_tokens` / `reasoning.output_tokens` | `completion_tokens` / `reasoning_tokens` |
| `gen_ai.response.finish_reasons` | `["length"]` when the run carries the `truncated` tag; absent otherwise |
| `pw_core.run_id`, `pw_core.agent_type`, `pw_core.job_id`, `pw_core.status` | the run row |
| `pw_core.prompt.system_id` / `user_id` | `system_prompt_id` / `user_prompt_id` |
| `pw_core.cost_usd` | `cost_usd` |
| `pw_core.cache_hit` | `true` when `status = 'cache_hit'` or the run has a `cache:*` tag |

**Input tokens are the semconv total.** pw-core's `prompt_tokens` is the *uncached* input; the convention defines `gen_ai.usage.input_tokens` as all input, cached included, with the cache counts as subsets of it. The export therefore sums the three columns; the uncached count is `input_tokens − cache_read − cache_creation`. Reasoning tokens are already inside `completion_tokens`, as the convention expects. Rows recorded by the OpenRouter client before v0.22.0 held cache-inclusive `prompt_tokens` and over-count here — see the upgrade note in [llm-tracking.md](llm-tracking.md#llmrunreporecord--atomic-write). Backends rely on this shape: Phoenix, for one, reads `gen_ai.usage.input_tokens` as its prompt total and the cache counts as parts of it.

**Errors.** A status other than `success` or `cache_hit` (`failed`, `budget_blocked`, a project's own `partial`…) makes the span status `ERROR` with the `error` text as its description, plus `error.type` (`error_class`, or the status when there is none), `pw_core.error_code` and `http.response.status_code`. Successful spans leave the status unset, as the convention asks of instrumentation.

---

## Prompt and response bodies

Bodies are **off by default**: a span carries no prompt or response text unless its agent type is named in `--include-bodies` (`include_bodies=` in Python). Name only agents whose text may leave the database for the backend.

For an included agent with a `llm_run_payloads` row:

- `gen_ai.input.messages` — JSON list of the rendered system and user prompts as `system` / `user` messages with one text part each.
- `gen_ai.output.messages` — JSON list with one `assistant` message holding `raw_response`. The semconv requires its `finish_reason`, and no column stores one, so it is derived: `length` for a run with the `truncated` tag, `error` for a run whose status is not `success` or `cache_hit`, otherwise `stop`.

`parsed_output` is not exported. Large bodies make large requests: if the exporter drops a batch as too large, lower `OTEL_BSP_MAX_EXPORT_BATCH_SIZE`.

---

## Failures and partial exports

If the exporter rejects a batch (endpoint down, 4xx, payload too large), `export_runs` raises `ClientError` and `pw-export` exits 1. The OTLP exporter logs the reason. Batches sent before the failure stay sent; fix the cause and re-run the same command — the [ids](#trace-shape-timing-and-ids) are unchanged, so the resend repeats spans rather than inventing new ones.

`--since` later than or equal to `--until` is an `InvalidInputError` (exit 1); an unparseable date is a usage error (exit 2).

---

## From Python

```python
from datetime import datetime

from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from pw_core.llm.tracking.otel import export_runs

summary = export_runs(
    since=datetime(2026, 10, 1),
    agent_type="summarizer",
    include_bodies=["summarizer"],
    exporter=ConsoleSpanExporter(),  # default: OTLP/HTTP from OTEL_EXPORTER_OTLP_*
)
print(summary.spans, summary.traces, summary.with_bodies)
```

Pass `resource=` to set the resource (and so the id scope) explicitly instead of from `OTEL_SERVICE_NAME` / `OTEL_RESOURCE_ATTRIBUTES`. A passed-in exporter is left open; the default one is shut down on return. In tests, `InMemorySpanExporter` collects the spans.

---

## Mounting in a project CLI

`pw_core.cli.export.app` is a plain Typer sub-app, mounted the way the jobs CLI is ([jobs.md](jobs.md)); `create_cli` is in [cli.md](cli.md):

```python
from pw_core.cli.export import app as export_app

app.add_typer(export_app, name="export")  # myapp export otel --dry-run
```

---

## Adding a new attribute

1. Map the column in `_attributes` in `src/pw_core/llm/tracking/otel.py`. Use the pinned convention's name when it defines one with the same meaning; otherwise use the `pw_core.` namespace. Never give a semconv name a different meaning than the convention's (the `input_tokens` sum above is the precedent).
2. Add the column to the exact-attribute assertion in `tests/test_llm_tracking_otel.py` and run it on SQLite, Postgres and MySQL (`PW_TEST_DATABASE_URL`).
3. Add the row to the [mapping table](#attribute-mapping).

When moving to a newer conventions release, change `SEMCONV_SCHEMA_URL`, re-check every name against that release, and update the version stated above.
