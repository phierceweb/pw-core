"""``pw-export`` — send tracked LLM runs to an observability backend.

``app`` mounts in a project's own CLI (``app.add_typer(export_app, name="export")`` gives
``myapp export otel``); ``main`` is the console script. Usage and the span mapping:
``docs/otel.md``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer
from rich.console import Console

from pw_core.cli import create_cli, run_cli
from pw_core.llm.tracking.otel import export_runs

_HELP = "Export tracked LLM runs to an observability backend."

app = typer.Typer(name="export", help=_HELP, no_args_is_help=True, add_completion=False)

_stdout = Console()


@app.callback()
def _group() -> None:
    """Keeps ``otel`` a subcommand: a one-command Typer app would otherwise run it as the root."""


def _iso(value: str | None, flag: str) -> dt.datetime | None:
    if value is None:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"{value!r} is not an ISO 8601 date or date-time", param_hint=flag)


@app.command("otel")
def otel(
    since: str | None = typer.Option(
        None, "--since", help="Runs created at or after this ISO 8601 time (UTC without offset)."
    ),
    until: str | None = typer.Option(
        None, "--until", help="Runs created before this ISO 8601 time (UTC without offset)."
    ),
    agent_type: str | None = typer.Option(None, "--agent-type", help="Only this agent type."),
    job_id: int | None = typer.Option(None, "--job-id", help="Only runs of this job."),
    include_bodies: list[str] | None = typer.Option(
        None,
        "--include-bodies",
        help="Comma-separated agent types whose prompt and response text is exported.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count the spans; send nothing."),
) -> None:
    """Send llm_runs as OpenTelemetry GenAI spans to the OTEL_EXPORTER_OTLP_* endpoint."""
    agents = [a.strip() for value in include_bodies or [] for a in value.split(",") if a.strip()]
    summary = export_runs(
        since=_iso(since, "--since"),
        until=_iso(until, "--until"),
        agent_type=agent_type,
        job_id=job_id,
        include_bodies=agents,
        dry_run=dry_run,
    )
    verb = "Would export" if dry_run else "Exported"
    _stdout.print(
        f"{verb} {summary.spans} span(s) in {summary.traces} trace(s); "
        f"{summary.with_bodies} with bodies."
    )


def main(args: list[str] | None = None) -> None:
    """Console-script entry: loads ``./.env`` first (shell variables win)."""
    dotenv = Path.cwd() / ".env"
    if dotenv.is_file():
        from dotenv import load_dotenv

        load_dotenv(dotenv)
    cli = create_cli("pw-export", help=_HELP)
    cli.registered_commands.extend(app.registered_commands)
    run_cli(cli, args=args)


__all__ = ["app", "main"]
