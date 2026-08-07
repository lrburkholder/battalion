"""Battalion CLI: run / resume / status."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from battalion.graph import run_ticket, resume_ticket
from battalion.interrupts.triggers import (
    TRIGGER_BUDGET_EXCEEDED,
    TRIGGER_INFRA_FAILURE,
    TRIGGER_MANUAL_CHECKPOINT,
    TRIGGER_ROLE_EDIT,
    TRIGGER_SAME_ROOT_CAUSE,
    TRIGGER_SCOPE_VIOLATION,
    get_trigger_name,
)
from battalion.progress import ProgressDisplay
from battalion.state.persistence import save_state, load_state
from battalion.state.models import RunState, RunStatus, Budget
from battalion.config import load_config, BattalionConfig

app = typer.Typer(
    name="battalion",
    help="Battalion SDLC Orchestrator — run, resume, and check status of tickets.",
    add_completion=False,
)

STATE_DIR = Path(".battalion/state")


def _state_path(run_id: str) -> Path:
    """Get the state file path for a run ID."""
    return STATE_DIR / f"{run_id}.json"


def _print_status(state: RunState, human: bool = False) -> None:
    """Print run status as JSON or human-readable."""
    if human:
        # Human-readable summary
        typer.echo(f"Run ID:      {state.run_id}")
        typer.echo(f"Ticket:      {state.ticket_id}")
        typer.echo(f"Status:      {state.status.value}")
        typer.echo(f"Phase:       {state.phase}")
        typer.echo(f"Budget:      {state.budget.used} / {state.budget.limit}")
        if state.manual_checkpoints:
            typer.echo(f"Checkpoints: {', '.join(state.manual_checkpoints)}")
        if state.interrupt_log:
            typer.echo("\nInterrupts:")
            for i, entry in enumerate(state.interrupt_log, 1):
                typer.echo(f"  {i}. {entry.trigger} @ {entry.timestamp.isoformat()}")
                if entry.resolution:
                    typer.echo(f"     Resolution: {entry.resolution}")
    else:
        # JSON output
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
            return f"{label}: the LLM call failed after all retries.\n   Provider error: {error}"
        return f"{label}: the LLM call failed after all retries."
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
    if state.status != RunStatus.AWAITING_HUMAN or not state.interrupt_log:
        return
    entry = state.interrupt_log[-1]
    typer.echo("\nRun paused — awaiting human review.")
    typer.echo(f"  {_describe_interrupt(entry)}")
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
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing state file"),
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
    
    # Prepare run ID and state path
    run_id = f"run-{ticket_id}"
    state_file = _state_path(run_id)
    
    # Check for existing state
    if state_file.exists() and not force:
        typer.echo(f"Error: State file already exists at {state_file}. Use --force to overwrite.", err=True)
        raise typer.Exit(1)
    
    # Create initial state
    initial_state = RunState(
        schema_version="1.0",
        run_id=run_id,
        ticket_id=ticket_id,
        status=RunStatus.NOT_STARTED,
        phase="architect",
        write_scope=cfg.write_scope,
        retry_bound=2,
        budget=Budget(limit=cfg.budget_limit, used=0),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=cfg.manual_checkpoints,
    )
    
    # Run the graph
    typer.echo(f"Starting run: {run_id}")
    display = ProgressDisplay()
    with display:
        final_state = RunState.model_validate(run_ticket(
            ticket_id=ticket_id,
            spec_text=spec_text,
            llm_configs=cfg.models,
            base_dir=cfg.base_dir,
            prompts_dir=cfg.prompts_dir,
            on_node_event=display.handle_event,
            on_token=display.handle_token,
        ))
    
    # Save final state
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    save_state(final_state, state_file)
    
    typer.echo(f"Run complete: {run_id} → {final_state.status.value}")
    typer.echo(f"State saved to: {state_file}")
    _print_pause_reason(final_state, run_id)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run ID (e.g., run-BTN-123)"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to battalion.config.yaml"),
    base_dir: str = typer.Option(".", "--base-dir", help="Base directory for file operations"),
    prompts_dir: str | None = typer.Option(None, "--prompts-dir", help="Directory containing node prompts"),
):
    """Resume a paused/interrupted run from saved state."""
    state_file = _state_path(run_id)
    
    if not state_file.exists():
        typer.echo(f"Error: No state file found at {state_file}", err=True)
        raise typer.Exit(1)
    
    # Load state
    state = load_state(state_file)
    
    if state.status != RunStatus.AWAITING_HUMAN:
        typer.echo(f"Warning: Run status is '{state.status.value}', not 'awaiting-human'. Resuming anyway.")
    
    # Load config
    cfg = load_config(config, {"base_dir": base_dir, "prompts_dir": prompts_dir})
    
    # Resume the run
    typer.echo(f"Resuming run: {run_id}")
    display = ProgressDisplay()
    with display:
        final_state = RunState.model_validate(resume_ticket(
            state=state,
            llm_configs=cfg.models,
            base_dir=cfg.base_dir,
            prompts_dir=cfg.prompts_dir,
            on_node_event=display.handle_event,
            on_token=display.handle_token,
        ))
    
    # Save updated state
    save_state(final_state, state_file)
    
    typer.echo(f"Resumed: {run_id} → {final_state.status.value}")
    typer.echo(f"State saved to: {state_file}")
    _print_pause_reason(final_state, run_id)


@app.command()
def status(
    run_id: str = typer.Argument(..., help="Run ID (e.g., run-BTN-123)"),
    human: bool = typer.Option(False, "--human", "-h", help="Human-readable output (default: JSON)"),
):
    """Show run status and interrupt history."""
    state_file = _state_path(run_id)
    
    if not state_file.exists():
        typer.echo(f"Error: No state file found at {state_file}", err=True)
        raise typer.Exit(1)
    
    state = load_state(state_file)
    _print_status(state, human=human)


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()