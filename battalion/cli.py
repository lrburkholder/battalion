"""Battalion CLI: run / resume / status / setup."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO
from uuid import UUID

import typer

from battalion.application import (
    ApplicationError,
    InspectRun,
    ResumeRun,
    RunAlreadyExists,
    StartRun,
    create_initial_state,
    inspect_run,
    resume_run,
    start_run,
    state_path,
)
from battalion.interrupts.triggers import (
    TRIGGER_BUDGET_EXCEEDED,
    TRIGGER_INFRA_FAILURE,
    TRIGGER_MANUAL_CHECKPOINT,
    TRIGGER_ROLE_EDIT,
    TRIGGER_ROLE_ESCALATION,
    TRIGGER_SAME_ROOT_CAUSE,
    TRIGGER_SCOPE_VIOLATION,
    get_trigger_name,
)
from battalion.progress import ProgressDisplay
from battalion.state.models import RunState, RunStatus
from battalion.recovery import assess_recovery
from battalion.config import load_config, DEFAULT_CONFIG_PATH
from battalion.llm.litellm_client import ModelDiversityError
from battalion.setup import (
    ConnectivityCheckFailed,
    MissingApiKey,
    ProviderNotDetected,
    run_setup,
)

TROUBLESHOOTING_URL = "https://lrburkholder.github.io/battalion/docs/troubleshooting.html"
INTERRUPT_GUIDES = {
    TRIGGER_INFRA_FAILURE: "infra-failure",
    TRIGGER_SCOPE_VIOLATION: "authority-stop",
    TRIGGER_ROLE_EDIT: "authority-stop",
    TRIGGER_BUDGET_EXCEEDED: "human-checkpoints",
    TRIGGER_MANUAL_CHECKPOINT: "human-checkpoints",
    TRIGGER_SAME_ROOT_CAUSE: "reviewer-tests",
    TRIGGER_ROLE_ESCALATION: "role-output",
}

app = typer.Typer(
    name="battalion",
    help=("Battalion SDLC Orchestrator - run, resume, and check status of tickets. "
          f"Troubleshooting: {TROUBLESHOOTING_URL}"),
    add_completion=False,
)

STATE_DIR = Path(".battalion/state")


def _print_troubleshooting(state: RunState) -> None:
    if assess_recovery(state) is not None:
        anchor = "resume-recovery"
    elif state.interrupt_log:
        anchor = INTERRUPT_GUIDES.get(state.interrupt_log[-1].trigger, "run-stopped")
    else:
        anchor = "run-stopped"
    typer.echo(f"Troubleshooting: {TROUBLESHOOTING_URL}#{anchor}")


def _state_path(run_id: str) -> Path:
    """Get the state file path for a run ID."""
    return state_path(run_id, STATE_DIR)


@contextmanager
def _open_trace_output(path: str | None) -> Iterator[tuple[TextIO | None, Path | None]]:
    """Open explicit CLI trace output without adding it to RunState."""
    if path is None:
        yield None, None
        return
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n", buffering=1) as stream:
        yield stream, target


def _print_status(
    state: RunState,
    human: bool = False,
    costs: bool = False,
    cost_summary: dict[str, object] | None = None,
) -> None:
    """Print run status as JSON or human-readable."""
    if human:
        # Human-readable summary
        if state.run_alias:
            typer.echo(f"Run:         {state.run_alias}")
        typer.echo(f"Run ID:      {state.run_id}")
        typer.echo(f"Ticket:      {state.ticket_id}")
        typer.echo(f"Status:      {state.status.value}")
        typer.echo(f"Phase:       {state.phase}")
        typer.echo(f"Budget:      {state.budget.used} / {state.budget.limit}")
        recovery = assess_recovery(state)
        if recovery is not None:
            typer.echo(f"Recovery:    {recovery.disposition}")
            typer.echo(recovery.message)
        if state.manual_checkpoints:
            typer.echo(f"Checkpoints: {', '.join(state.manual_checkpoints)}")
        if state.interrupt_log:
            typer.echo("\nInterrupts:")
            for i, entry in enumerate(state.interrupt_log, 1):
                typer.echo(f"  {i}. {entry.trigger} @ {entry.timestamp.isoformat()}")
                if entry.resolution:
                    typer.echo(f"     Resolution: {entry.resolution}")
        role_results = [
            execution for execution in state.execution_record.node_executions
            if execution.role_result is not None
        ]
        if role_results:
            typer.echo("\nRole results:")
            for execution in role_results:
                result = execution.role_result
                detail = result.reason_code.value if result.reason_code else None
                if result.summary:
                    detail = f"{detail}; {result.summary}" if detail else result.summary
                typer.echo(
                    f"  {execution.phase}: {result.kind.value}"
                    + (f" ({detail})" if detail else "")
                )
        if costs:
            summary = cost_summary or {}
            typer.echo("\nLLM costs:")
            for phase in summary["phases"]:
                known = ", ".join(
                    f"{cost['amount']} {cost['currency']}" for cost in phase["costs"]
                ) or "unknown"
                if phase["unknown_cost_calls"]:
                    known += f"; {phase['unknown_cost_calls']} unknown"
                typer.echo(
                    f"  {phase['phase']} [{', '.join(phase['models'])}]: "
                    f"{phase['calls']} call(s), "
                    f"{phase['input_tokens']} in / {phase['output_tokens']} out, "
                    f"{phase['streamed_reasoning_characters']} reasoning chars / "
                    f"{phase['streamed_content_characters']} content chars, "
                    f"{known}"
                )
            known = ", ".join(
                f"{cost['amount']} {cost['currency']}" for cost in summary["costs"]
            ) or "unknown"
            if summary["unknown_cost_calls"]:
                known += f"; {summary['unknown_cost_calls']} unknown"
            typer.echo(
                f"  Total: {summary['calls']} call(s), "
                f"{summary['input_tokens']} in / {summary['output_tokens']} out, "
                f"{summary['streamed_reasoning_characters']} reasoning chars / "
                f"{summary['streamed_content_characters']} content chars, "
                f"{known}"
            )
        if recovery is not None or state.status in {
            RunStatus.AWAITING_HUMAN, RunStatus.BLOCKED, RunStatus.FAILED_INFRA,
        }:
            _print_troubleshooting(state)
    else:
        if costs:
            typer.echo(json.dumps(cost_summary or {}, indent=2))
        else:
            typer.echo(state.model_dump_json(indent=2))


def _describe_interrupt(entry) -> str:
    """Build a one-paragraph human explanation of a logged interrupt.

    A paused run must tell the human WHY it paused — the raw JSON state file
    is the source of truth, but the terminal is where the answer needs to
    appear (e.g. "architect's Mistral call failed after 3 attempts: invalid
    API key", not just "awaiting-human")."""
    context = entry.context or {}
    trigger = entry.trigger
    label = get_trigger_name(trigger)

    if trigger == TRIGGER_INFRA_FAILURE:
        error = context.get("error")
        if error:
            return f"{label}: execution failed.\n   Recorded error: {error}"
        return f"{label}: execution failed; inspect the saved attempt and interrupt context."
    if trigger == TRIGGER_SCOPE_VIOLATION:
        error = context.get("error")
        if error:
            return f"{label}: a node attempted an out-of-scope write.\n   {error}"
        return f"{label}: a node attempted an out-of-scope write."
    if trigger == TRIGGER_BUDGET_EXCEEDED:
        return f"{label}: budget spent ({context.get('used', '?')}/{context.get('limit', '?')})."
    if trigger == TRIGGER_SAME_ROOT_CAUSE:
        cause = context.get("cause")
        if cause:
            return f"{label} (cycle {context.get('cycle_number')}): \"{cause}\""
        return f"{label}: the same root cause was rejected twice."
    if trigger == TRIGGER_ROLE_EDIT:
        return f"{label}: a node's write scope changed mid-run."
    if trigger == TRIGGER_MANUAL_CHECKPOINT:
        return f"{label}: paused at declared phase {context.get('phase', '?')}."
    return label


def _print_pause_reason(state: RunState, run_id: str) -> None:
    """After a run/resume pauses, print why it paused and how to continue."""
    recovery = assess_recovery(state)
    if recovery is not None:
        typer.echo(f"Recovery: {recovery.disposition}. {recovery.message}")
        _print_troubleshooting(state)
        return
    if state.status != RunStatus.AWAITING_HUMAN or not state.interrupt_log:
        return
    entry = state.interrupt_log[-1]
    typer.echo("\nRun paused - awaiting human review.")
    typer.echo(f"  {_describe_interrupt(entry)}")
    _print_troubleshooting(state)
    typer.echo(f"  Resume when ready: battalion resume {run_id}")


def _load_spec_text(spec_path: str) -> str:
    """Load spec text from file or return the string directly."""
    path = Path(spec_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    # If not a file, treat as literal spec text
    return spec_path


@app.command()
def run(
    ticket_id: str = typer.Argument(..., help="Ticket ID (e.g., BTN-123)"),
    spec: str = typer.Option(..., "--spec", "-s", help="Path to spec file or spec text"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to battalion.config.yaml"),
    model_architect: str | None = typer.Option(None, "--model-architect"),
    model_driver: str | None = typer.Option(None, "--model-driver"),
    model_reviewer: str | None = typer.Option(None, "--model-reviewer"),
    model_refactorer: str | None = typer.Option(None, "--model-refactorer"),
    budget_limit: int | None = typer.Option(None, "--budget", help="Budget limit (overrides config)"),
    manual_checkpoint: list[str] | None = typer.Option(None, "--checkpoint", help="Manual checkpoint phase(s)"),
    base_dir: str = typer.Option(".", "--base-dir", help="Base directory for file operations"),
    prompts_dir: str | None = typer.Option(None, "--prompts-dir", help="Directory containing node prompts"),
    trace_output: str | None = typer.Option(
        None,
        "--trace-output",
        help="Append raw token/reasoning observations to this JSONL file",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Authorize overwrite if a canonical ID already exists"),
):
    """Start a new ticket run through the Battalion graph."""
    # Load configuration with CLI overrides
    cli_overrides = {
        "model_architect": model_architect,
        "model_driver": model_driver,
        "model_reviewer": model_reviewer,
        "model_refactorer": model_refactorer,
        "budget_limit": budget_limit,
        "manual_checkpoints": manual_checkpoint,
        "base_dir": base_dir,
        "prompts_dir": prompts_dir,
    }
    # Remove None values
    cli_overrides = {k: v for k, v in cli_overrides.items() if v is not None}
    
    cfg = load_config(config, cli_overrides)
    
    # Load spec text
    spec_text = _load_spec_text(spec)
    
    try:
        initial_state = create_initial_state(ticket_id, spec_text, cfg)
        run_id = initial_state.run_id
        typer.echo(f"Starting run: {initial_state.run_alias} ({run_id})")
        with _open_trace_output(trace_output) as (trace_stream, trace_path):
            if trace_path is not None:
                typer.echo(f"Trace output: {trace_path}")
            display = ProgressDisplay(trace_output=trace_stream, run_ref=run_id)
            with display:
                result = start_run(
                    StartRun(initial_state=initial_state, config=cfg, overwrite=force),
                    state_dir=STATE_DIR,
                    on_node_event=display.handle_event,
                    on_token=display.handle_token,
                )
    except OSError as exc:
        typer.echo(f"Error: Cannot write trace output: {exc}", err=True)
        raise typer.Exit(1)
    except RunAlreadyExists as exc:
        typer.echo(
            f"Error: State file already exists at {exc.path}. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    except ApplicationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    
    typer.echo(f"Run complete: {result.run_id} -> {result.state.status.value}")
    typer.echo(f"State saved to: {result.state_path}")
    _print_pause_reason(result.state, result.run_id)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Canonical UUID or legacy run ID"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to battalion.config.yaml"),
    base_dir: str = typer.Option(".", "--base-dir", help="Base directory for file operations"),
    prompts_dir: str | None = typer.Option(None, "--prompts-dir", help="Directory containing node prompts"),
    trace_output: str | None = typer.Option(
        None,
        "--trace-output",
        help="Append raw token/reasoning observations to this JSONL file",
    ),
    actor_id: UUID | None = typer.Option(
        None,
        "--actor-id",
        help="Durable Actor ID; defaults to the selected local human Actor",
    ),
    resolution: str = typer.Option(
        "authorized resume", "--resolution", help="Durable resolution for the latest interrupt"
    ),
    action_id: str | None = typer.Option(
        None, "--action-id", help="Stable request ID for idempotent resume replay",
    ),
):
    """Resume a paused/interrupted run from saved state."""
    cfg = load_config(config, {"base_dir": base_dir, "prompts_dir": prompts_dir})
    typer.echo(f"Resuming run: {run_id}")
    try:
        with _open_trace_output(trace_output) as (trace_stream, trace_path):
            if trace_path is not None:
                typer.echo(f"Trace output: {trace_path}")
            display = ProgressDisplay(trace_output=trace_stream, run_ref=run_id)
            with display:
                result = resume_run(
                    ResumeRun(
                        run_id=run_id,
                        config=cfg,
                        actor_id=actor_id,
                        resolution=resolution,
                        action_id=action_id,
                    ),
                    state_dir=STATE_DIR,
                    on_node_event=display.handle_event,
                    on_token=display.handle_token,
                )
    except OSError as exc:
        typer.echo(f"Error: Cannot write trace output: {exc}", err=True)
        raise typer.Exit(1)
    except ApplicationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    
    if result.warning:
        typer.echo(f"Warning: {result.warning}")
    typer.echo(f"Resumed: {result.run_id} -> {result.state.status.value}")
    typer.echo(f"State saved to: {result.state_path}")
    _print_pause_reason(result.state, result.run_id)


@app.command()
def status(
    run_id: str = typer.Argument(..., help="Canonical UUID or legacy run ID"),
    human: bool = typer.Option(False, "--human", "-h", help="Human-readable output (default: JSON)"),
    costs: bool = typer.Option(False, "--costs", help="Include per-phase LLM token and dollar costs"),
):
    """Show run status and interrupt history."""
    try:
        result = inspect_run(InspectRun(run_id=run_id), state_dir=STATE_DIR)
    except ApplicationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    _print_status(
        result.state,
        human=human,
        costs=costs,
        cost_summary=result.costs,
    )


def _prompt_value(message: str, default: str) -> str:
    """Interactive prompt; returns the entered value or the default."""
    try:
        value = input(f"{message} [{default}]: ").strip()
    except EOFError:
        return default
    return value or default


@app.command()
def setup(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to battalion.config.yaml (default: ./battalion.config.yaml)"),
    model_architect: str | None = typer.Option(None, "--model-architect"),
    model_driver: str | None = typer.Option(None, "--model-driver"),
    model_reviewer: str | None = typer.Option(None, "--model-reviewer"),
    model_refactorer: str | None = typer.Option(None, "--model-refactorer"),
    validate: bool = typer.Option(True, "--validate/--no-validate", help="Run live connectivity checks before saving"),
):
    """Configure LLM providers and validate connectivity, writing battalion.config.yaml."""
    overrides = {
        "architect": model_architect,
        "driver": model_driver,
        "reviewer": model_reviewer,
        "refactorer": model_refactorer,
    }
    interactive = sys.stdin.isatty()
    try:
        written = run_setup(
            config_path=config or DEFAULT_CONFIG_PATH,
            model_overrides=overrides,
            validate=validate,
            prompt=_prompt_value if interactive else None,
            echo=typer.echo,
        )
    except ProviderNotDetected as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except MissingApiKey as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except ConnectivityCheckFailed as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except ModelDiversityError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Setup complete. Config written to: {config or DEFAULT_CONFIG_PATH}")
    for node in ("architect", "driver", "reviewer", "refactorer"):
        typer.echo(f"  {node}: {written[node]['model']}")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
