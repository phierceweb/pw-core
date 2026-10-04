"""``pf-eval`` — replay a golden set or compare two experiments, as a CI gate.

``app`` mounts in a project's own CLI; ``main`` is the console script. Exit codes and usage:
``docs/eval-harness.md``.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from pf_core.cli import create_cli, run_cli
from pf_core.eval import EvalRunner, GoldenSetRepo, load_eval_config
from pf_core.exceptions import AppError, ConfigurationError, InvalidInputError, PreconditionError
from pf_core.log import log_exception

_HELP = "Golden-set replay and regression gate."

app = typer.Typer(name="eval", help=_HELP, no_args_is_help=True, add_completion=False)

_stdout = Console()
_stderr = Console(stderr=True)

_EXPECTED_ERRORS = (ConfigurationError, InvalidInputError, PreconditionError)
_TARGET_KEYS = ("model", "backend")


def _cannot_evaluate(exc: Exception) -> typer.Exit:
    """Exit 2 for anything that kept the eval from finishing; 1 means it ran and failed."""
    if not isinstance(exc, _EXPECTED_ERRORS):
        log_exception(AppError("pf-eval could not evaluate", cause=exc))
    _stderr.print(f"[red]Could not evaluate:[/red] {escape(str(exc))}")
    return typer.Exit(2)


def _prepare_output(output: str) -> None:
    """Fail before any replay spends tokens if the HTML report could not be written."""
    try:
        import jinja2  # noqa: F401
    except ImportError as exc:
        raise ConfigurationError("--output needs jinja2, which the [eval] extra installs") from exc
    try:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(f"cannot create the --output directory: {exc}") from exc


def _parse_target(pairs: list[str]) -> dict[str, str]:
    """Only ``model`` and ``backend``: values arrive as strings, and the client forwards unknown
    keys to the provider, which may ignore them."""
    out: dict[str, str] = {}
    for item in pairs:
        key, sep, value = item.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in _TARGET_KEYS or not value:
            raise typer.BadParameter(
                f"expected model=<slug> or backend=<name>, got {item!r}", param_hint="--target"
            )
        if key in out:
            raise typer.BadParameter(f"{key} is given more than once", param_hint="--target")
        out[key] = value
    return out


@app.command("run")
def run_eval(
    version: str = typer.Option(..., "--version", help="Golden set version, e.g. golden_v1."),
    agent_type: str = typer.Option(..., "--agent-type", help="Agent type slug."),
    target: list[str] = typer.Option(
        [], "--target", help="Override as key=value: model=<slug> and/or backend=<name>."
    ),
    tag_as: str | None = typer.Option(None, "--tag-as", help="Experiment tag for the replays."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write an HTML report here."),
    config: str | None = typer.Option(None, "--config", help="Path to eval.yaml."),
) -> None:
    """Replay a golden set. Fails unless every golden replayed and the mean score passed."""
    target_dict = _parse_target(target)
    targets = "  ".join(f"{k}={v}" for k, v in target_dict.items()) or "router config"
    _stderr.print(
        f"[dim]Running eval: {escape(agent_type)} vs {escape(version)} ({escape(targets)})[/dim]"
    )
    try:
        if output:
            _prepare_output(output)
        report = EvalRunner(config_path=config).run(
            version=version, agent_type=agent_type, target=target_dict, tag_as=tag_as
        )
    except Exception as exc:
        raise _cannot_evaluate(exc) from exc

    typer.echo(report.summary())
    if output:
        try:
            report.write_html(output)
        except OSError as exc:
            _stderr.print(f"[red]Could not write the report:[/red] {escape(str(exc))}")
            raise typer.Exit(2) from exc
        _stderr.print(f"[dim]HTML report written to {escape(output)}[/dim]")
    if not report.passed:
        raise typer.Exit(1)


@app.command("compare")
def compare_experiments(
    baseline: str = typer.Option(..., "--baseline", help="Experiment tag of the baseline."),
    candidate: str = typer.Option(..., "--candidate", help="Experiment tag of the candidate."),
    agent_type: str = typer.Option(..., "--agent-type", help="Agent type slug."),
    config: str | None = typer.Option(None, "--config", help="Path to eval.yaml."),
) -> None:
    """Pair two experiments by golden. Fails if the candidate is worse or left a golden unscored."""
    try:
        result = EvalRunner(config_path=config).compare_experiments(
            baseline=baseline, candidate=candidate, agent_type=agent_type
        )
    except Exception as exc:
        raise _cannot_evaluate(exc) from exc

    pairs = result["pairs"]
    table = Table(title=escape(f"{baseline} vs {candidate} ({agent_type})"))
    table.add_column("Golden ID", style="dim")
    table.add_column("Baseline", justify="right")
    table.add_column("Candidate", justify="right")
    table.add_column("Delta", justify="right")
    for p in sorted(pairs, key=lambda x: x["delta"]):
        color = "green" if p["delta"] >= 0 else "red"
        table.add_row(
            str(p["golden_id"]),
            f"{p['baseline_score']:.3f}",
            f"{p['candidate_score']:.3f}",
            f"[{color}]{p['delta']:+.3f}[/{color}]",
        )
    _stdout.print(table)

    deltas = [p["delta"] for p in pairs]
    mean_delta = statistics.mean(deltas) if deltas else None
    counts = (
        f"Pairs: {len(pairs)}   Scored runs: {result['baseline_runs']} baseline / "
        f"{result['candidate_runs']} candidate"
    )
    if mean_delta is not None:
        counts = (
            f"Mean delta: {mean_delta:+.3f}   Median: {statistics.median(deltas):+.3f}   {counts}"
        )
    typer.echo(counts)
    unscored = result["baseline_only"]
    if unscored:
        _stderr.print(
            f"[red]FAIL:[/red] the candidate has no score for {len(unscored)} golden(s) "
            f"the baseline scored: {escape(str(unscored[:10]))}"
        )
        raise typer.Exit(1)
    if mean_delta is not None and mean_delta < 0:
        _stderr.print(f"[red]FAIL:[/red] the candidate scores lower (mean delta {mean_delta:+.3f})")
        raise typer.Exit(1)


@app.command("list-golden")
def list_golden(
    version: str = typer.Option(..., "--version", help="Golden set version."),
    agent_type: str | None = typer.Option(None, "--agent-type", help="Filter by agent type."),
    limit: int = typer.Option(50, "--limit", help="Max rows to display."),
    config: str | None = typer.Option(None, "--config", help="Path to eval.yaml."),
) -> None:
    """List golden runs for a version."""
    load_eval_config(config)
    rows = GoldenSetRepo().list(version=version, agent_type=agent_type, limit=limit)
    if not rows:
        _stderr.print(f"[yellow]No golden runs for version={escape(repr(version))}.[/yellow]")
        return

    table = Table(title=escape(f"Golden set: {version}"))
    table.add_column("Run ID")
    for column in ("Agent type", "Model", "Created at", "Status"):
        table.add_column(column, style="dim")
    for r in rows:
        table.add_row(
            str(r["id"]),
            str(r.get("agent_type_slug", "")),
            str(r.get("model_name", "")),
            str(r.get("created_at", ""))[:19],
            str(r.get("status", "")),
        )
    _stdout.print(table)
    _stdout.print(f"[dim]{len(rows)} row(s) shown[/dim]")


@app.command("seed")
def seed(
    version: str = typer.Option(..., "--version", help="Golden set version, e.g. golden_v1."),
    outcome_kind: str = typer.Option(
        ..., "--outcome-kind", help="Outcome kind to seed from, e.g. summary_accepted."
    ),
    agent_type: str | None = typer.Option(None, "--agent-type", help="Filter by agent type."),
    limit: int = typer.Option(50, "--limit", help="Max runs to seed."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List candidates without promoting."),
    config: str | None = typer.Option(None, "--config", help="Path to eval.yaml."),
) -> None:
    """Bulk-seed the golden set from runs that have a given outcome."""
    load_eval_config(config)
    run_ids = GoldenSetRepo().seed_from_outcomes(
        version=version,
        outcome_kind=outcome_kind,
        agent_type=agent_type,
        limit=limit,
        dry_run=dry_run,
    )
    if not run_ids:
        scope = f" and agent_type={agent_type!r}" if agent_type else ""
        _stderr.print(
            f"[yellow]No runs found with outcome_kind={escape(repr(outcome_kind))}"
            f"{escape(scope)}.[/yellow]"
        )
        return

    label = "Candidates (dry-run)" if dry_run else "Seeded"
    table = Table(title=escape(f"{label}: {version} ← outcome_kind={outcome_kind!r}"))
    table.add_column("Run ID")
    for rid in run_ids:
        table.add_row(str(rid))
    _stdout.print(table)
    _stdout.print(f"[dim]{len(run_ids)} run(s) {'found' if dry_run else 'promoted'}.[/dim]")


@app.command("promote")
def promote(
    run_id: int = typer.Argument(..., help="llm_runs.id to promote."),
    version: str = typer.Option(..., "--version", help="Golden set version."),
    notes: str | None = typer.Option(None, "--notes", help="Optional note."),
    config: str | None = typer.Option(None, "--config", help="Path to eval.yaml."),
) -> None:
    """Add a run to the golden set."""
    load_eval_config(config)
    GoldenSetRepo().add(run_id=run_id, version=version, notes=notes)
    _stdout.print(f"[green]Run {run_id} promoted to {escape(version)}.[/green]")


@app.command("demote")
def demote(
    run_id: int = typer.Argument(..., help="llm_runs.id to demote."),
    version: str = typer.Option(..., "--version", help="Golden set version."),
    config: str | None = typer.Option(None, "--config", help="Path to eval.yaml."),
) -> None:
    """Remove a run from the golden set (tag removed; outcomes and metrics kept)."""
    load_eval_config(config)
    GoldenSetRepo().remove(run_id=run_id, version=version)
    _stdout.print(f"[yellow]Run {run_id} removed from {escape(version)}. History kept.[/yellow]")


def main(args: list[str] | None = None) -> None:
    """Console-script entry: loads ``./.env`` (shell variables win) and puts the working
    directory on ``sys.path`` so eval.yaml's ``imports`` resolve, as a project script would."""
    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    dotenv = root / ".env"
    if dotenv.is_file():
        from dotenv import load_dotenv

        load_dotenv(dotenv)
    cli = create_cli("pf-eval", help=_HELP)
    cli.registered_commands.extend(app.registered_commands)
    run_cli(cli, args=args)
