"""LangGraph StateGraph wiring for Battalion (BTN-7).

Wires Architect, Driver (RED/GREEN modes), Reviewer, and Refactorer into a
StateGraph with correct edges and interrupt pause points.

Flow:
  Architect -> Driver(RED) -> Reviewer(RED_CHECK) -> 
    [if accepted] -> Driver(GREEN) -> Reviewer(GREEN_CHECK) -> 
    [if accepted] -> Refactorer -> Reviewer(REFACTOR_CHECK) -> 
    [if accepted] -> DONE
    [if rejected] -> back to appropriate Driver/Refactorer phase

Interrupt triggers (from spec.md v1 taxonomy):
  1. Same root cause rejected twice (per-checkpoint-type)
  2. Out-of-scope write attempt (defense-in-depth)
  3. Budget exceeded (per-graph-run)
  4. Role-definition edit
  5. Infra failure (LLM call fails after retries)
  6. Manual checkpoint (user-declared pause point)

When any interrupt fires, the graph transitions to AWAITING_HUMAN status
and pauses. The CLI (BTN-9) will handle resumption.
"""
from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from battalion.interrupts.budget import increment_budget
from battalion.interrupts.triggers import (
    TRIGGER_SAME_ROOT_CAUSE,
    check_any_trigger,
    log_interrupt,
)
from battalion.state.models import (
    CheckpointType,
    RejectionRecord,
    RunState,
    RunStatus,
)

# --- Node names (also used as phase names in RunState) ---
NODE_ARCHITECT = "architect"
NODE_DRIVER_RED = "driver_red"
NODE_DRIVER_GREEN = "driver_green"
NODE_REVIEWER_RED = "reviewer_red"
NODE_REVIEWER_GREEN = "reviewer_green"
NODE_REFACTORER = "refactorer"
NODE_REVIEWER_REFACTOR = "reviewer_refactor"
NODE_DONE = "done"
NODE_PAUSE = "awaiting_human"

# Phase to node name mapping for state transitions
PHASE_TO_NODE = {
    "architect": NODE_ARCHITECT,
    "driver": NODE_DRIVER_RED,  # Default driver mode
    "reviewer": NODE_REVIEWER_RED,  # Default reviewer checkpoint
    "refactorer": NODE_REFACTORER,
}

# Node name to phase mapping (what phase to set in state after node completes)
NODE_TO_PHASE = {
    NODE_ARCHITECT: "driver",
    NODE_DRIVER_RED: "reviewer",
    NODE_DRIVER_GREEN: "reviewer",
    NODE_REVIEWER_RED: "driver",
    NODE_REVIEWER_GREEN: "refactorer",
    NODE_REFACTORER: "reviewer",
    NODE_REVIEWER_REFACTOR: "done",
}

# Node to checkpoint type mapping (for Reviewer nodes)
NODE_TO_CHECKPOINT = {
    NODE_REVIEWER_RED: CheckpointType.RED_CHECK,
    NODE_REVIEWER_GREEN: CheckpointType.GREEN_CHECK,
    NODE_REVIEWER_REFACTOR: CheckpointType.REFACTOR_CHECK,
}

# Resume target mapping from checkpoint type to node name
CHECKPOINT_TO_RESUME_NODE = {
    CheckpointType.RED_CHECK: NODE_DRIVER_RED,
    CheckpointType.GREEN_CHECK: NODE_DRIVER_GREEN,
    CheckpointType.REFACTOR_CHECK: NODE_REFACTORER,
}


def _make_architect_node(
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
) -> Callable[[RunState], RunState]:
    """Create the Architect node function for the graph."""
    from battalion.nodes.architect import run_architect
    from battalion.prompts.loader import load_system_prompt
    
    def node(state: RunState) -> RunState:
        # Increment budget for this LLM call
        state = increment_budget(state)
        
        # Run Architect node
        # Note: spec_text comes from the initial state or ticket
        spec_text = state.ticket_id  # Simplified for now; real impl would load spec
        
        new_state = run_architect(
            state=state,
            spec_text=spec_text,
            llm_config=llm_configs.get("architect", llm_configs.get("default")),
            base_dir=base_dir,
            prompts_dir=prompts_dir,
        )
        
        # Check interrupts after node execution
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=NODE_TO_PHASE[NODE_ARCHITECT]
        )
        
        if should_pause:
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
        
        return new_state
    
    return node


def _make_driver_node(
    mode: str,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
) -> Callable[[RunState], RunState]:
    """Create a Driver node function for the graph.
    
    Args:
        mode: "red" or "green" for BTN-11 RED/GREEN mode support
    """
    from battalion.nodes.driver import run_driver
    from battalion.prompts.loader import load_system_prompt
    
    def node(state: RunState) -> RunState:
        # Increment budget for this LLM call
        state = increment_budget(state)
        
        # Determine ticket text from state
        # For now, use ticket_id as the input; real impl would use full ticket
        ticket_text = state.ticket_id
        
        new_state = run_driver(
            state=state,
            ticket_text=ticket_text,
            llm_config=llm_configs.get("driver", llm_configs.get("default")),
            base_dir=base_dir,
            mode=mode,
            prompts_dir=prompts_dir,
        )
        
        # Driver always transitions to reviewer
        # The specific reviewer checkpoint is determined by the graph edges
        next_phase = NODE_TO_PHASE.get(f"driver_{mode}", "reviewer")
        
        # Check interrupts
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=next_phase
        )
        
        if should_pause:
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
        
        return new_state
    
    return node


def _make_reviewer_node(
    checkpoint: CheckpointType,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
) -> Callable[[RunState], RunState]:
    """Create a Reviewer node function for the graph.
    
    Args:
        checkpoint: The checkpoint type (RED_CHECK, GREEN_CHECK, REFACTOR_CHECK)
    """
    from battalion.nodes.reviewer import run_reviewer
    from battalion.prompts.loader import load_system_prompt
    
    def node(state: RunState) -> RunState:
        # Reviewer doesn't call LLM for the actual test run, but does for
        # rejection cause articulation. Budget increment for the LLM call.
        state = increment_budget(state)
        
        new_state = run_reviewer(
            state=state,
            base_dir=base_dir,
            llm_config=llm_configs.get("reviewer", llm_configs.get("default")),
            checkpoint=checkpoint,
            prompts_dir=prompts_dir,
        )
        
        # Reviewer sets the next phase based on accept/reject
        # But we need to check interrupts first
        next_phase = new_state.phase
        
        # Check interrupts
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=next_phase
        )
        
        if should_pause:
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
        
        return new_state
    
    return node


def _make_refactorer_node(
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
) -> Callable[[RunState], RunState]:
    """Create the Refactorer node function for the graph."""
    from battalion.nodes.refactorer import run_refactorer
    from battalion.prompts.loader import load_system_prompt
    
    def node(state: RunState) -> RunState:
        # Increment budget for this LLM call
        state = increment_budget(state)
        
        # Refactor text from state
        refactor_text = state.ticket_id  # Simplified
        
        new_state = run_refactorer(
            state=state,
            refactor_text=refactor_text,
            llm_config=llm_configs.get("refactorer", llm_configs.get("driver", llm_configs.get("default"))),
            base_dir=base_dir,
            prompts_dir=prompts_dir,
        )
        
        # Check interrupts
        next_phase = NODE_TO_PHASE[NODE_REFACTORER]
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=next_phase
        )
        
        if should_pause:
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
        
        return new_state
    
    return node


def _make_done_node() -> Callable[[RunState], RunState]:
    """Create the terminal DONE node."""
    def node(state: RunState) -> RunState:
        return state.model_copy(update={
            "status": RunStatus.DONE,
            "phase": "done",
        })
    return node


def _make_pause_node() -> Callable[[RunState], RunState]:
    """Create the PAUSE node for when interrupts fire."""
    def node(state: RunState) -> RunState:
        # Already in AWAITING_HUMAN status from log_interrupt
        # Just ensure phase is set correctly
        return state.model_copy(update={
            "status": RunStatus.AWAITING_HUMAN,
            "phase": NODE_PAUSE,
        })
    return node


def _should_check_trigger_1(state: RunState) -> str:
    """Route condition: check if trigger #1 (same root cause twice) fires.
    
    If it fires, route to pause node. Otherwise, continue.
    """
    from battalion.interrupts.triggers import check_reviewer_rejection
    
    should_pause, _ = check_reviewer_rejection(state)
    if should_pause:
        return NODE_PAUSE
    return "continue"


def build_graph(
    llm_configs: dict[str, Any],
    base_dir: str = ".",
    prompts_dir: str | None = None,
) -> StateGraph:
    """Build the Battalion StateGraph with all nodes and edges.
    
    The graph implements the RED -> Reviewer -> GREEN -> Reviewer -> 
    Refactorer -> Reviewer loop from plan.md ADR-006 through ADR-009.
    
    Args:
        llm_configs: Per-node LLM configurations (keys: "architect", "driver", 
                     "reviewer", "refactorer", or "default")
        base_dir: Base directory for file operations
        prompts_dir: Directory containing node system prompts
    
    Returns:
        Configured StateGraph ready to run
    """
    graph = StateGraph(RunState)
    
    # --- Create node functions ---
    architect_node = _make_architect_node(llm_configs, base_dir, prompts_dir)
    driver_red_node = _make_driver_node("red", llm_configs, base_dir, prompts_dir)
    driver_green_node = _make_driver_node("green", llm_configs, base_dir, prompts_dir)
    reviewer_red_node = _make_reviewer_node(CheckpointType.RED_CHECK, llm_configs, base_dir, prompts_dir)
    reviewer_green_node = _make_reviewer_node(CheckpointType.GREEN_CHECK, llm_configs, base_dir, prompts_dir)
    refactorer_node = _make_refactorer_node(llm_configs, base_dir, prompts_dir)
    reviewer_refactor_node = _make_reviewer_node(CheckpointType.REFACTOR_CHECK, llm_configs, base_dir, prompts_dir)
    done_node = _make_done_node()
    pause_node = _make_pause_node()
    
    # --- Register nodes ---
    graph.add_node(NODE_ARCHITECT, architect_node)
    graph.add_node(NODE_DRIVER_RED, driver_red_node)
    graph.add_node(NODE_DRIVER_GREEN, driver_green_node)
    graph.add_node(NODE_REVIEWER_RED, reviewer_red_node)
    graph.add_node(NODE_REVIEWER_GREEN, reviewer_green_node)
    graph.add_node(NODE_REFACTORER, refactorer_node)
    graph.add_node(NODE_REVIEWER_REFACTOR, reviewer_refactor_node)
    graph.add_node(NODE_DONE, done_node)
    graph.add_node(NODE_PAUSE, pause_node)
    
    # --- Define edges ---
    
    # Architect -> Driver(RED)
    graph.add_edge(NODE_ARCHITECT, NODE_DRIVER_RED)
    
    # Driver(RED) -> Reviewer(RED_CHECK)
    graph.add_edge(NODE_DRIVER_RED, NODE_REVIEWER_RED)
    
    # Reviewer(RED_CHECK) conditional edges
    # The Reviewer node itself sets state.phase based on accept/reject
    # If accepted (tests fail as expected): phase = "driver" -> Driver(GREEN)
    # If rejected (tests pass unexpectedly): phase = "driver" -> Driver(RED) to retry
    # We route based on the phase set by the Reviewer
    graph.add_conditional_edges(
        NODE_REVIEWER_RED,
        lambda state: NODE_DRIVER_GREEN if state.phase == "driver" else NODE_PAUSE,
        [NODE_DRIVER_GREEN, NODE_PAUSE],
    )
    # Default edge if somehow no condition matches
    graph.add_edge(NODE_REVIEWER_RED, NODE_DRIVER_RED)
    
    # Driver(GREEN) -> Reviewer(GREEN_CHECK)
    graph.add_edge(NODE_DRIVER_GREEN, NODE_REVIEWER_GREEN)
    
    # Reviewer(GREEN_CHECK) conditional edges
    # If accepted (tests pass): -> Refactorer
    # If rejected (tests fail): -> Driver(GREEN) to retry
    graph.add_conditional_edges(
        NODE_REVIEWER_GREEN,
        lambda state: NODE_REFACTORER if state.phase == "refactorer" else NODE_DRIVER_GREEN,
        [NODE_REFACTORER, NODE_DRIVER_GREEN],
    )
    # Default edge if somehow no condition matches
    graph.add_edge(NODE_REVIEWER_GREEN, NODE_DRIVER_GREEN)
    
    # Refactorer -> Reviewer(REFACTOR_CHECK)
    graph.add_edge(NODE_REFACTORER, NODE_REVIEWER_REFACTOR)
    
    # Reviewer(REFACTOR_CHECK) conditional edges
    # If accepted (tests still pass): -> DONE
    # If rejected (tests fail): -> Refactorer to retry
    graph.add_conditional_edges(
        NODE_REVIEWER_REFACTOR,
        lambda state: NODE_DONE if state.phase == "done" else NODE_REFACTORER,
        [NODE_DONE, NODE_REFACTORER],
    )
    # Default edge if somehow no condition matches
    graph.add_edge(NODE_REVIEWER_REFACTOR, NODE_REFACTORER)
    
    # PAUSE node is a sink - no outgoing edges until resume
    # The CLI (BTN-9) will handle loading saved state and continuing
    
    # DONE is terminal - use END constant
    graph.add_edge(NODE_DONE, END)
    
    # Set entry point
    graph.set_entry_point(NODE_ARCHITECT)
    
    # Add conditional edges from PAUSE node for resume support
    # The resume_target field in state determines where to continue
    def _resume_router(state: RunState) -> str:
        """Route from PAUSE node based on resume_target."""
        target = state.resume_target
        if target in (NODE_DRIVER_RED, NODE_DRIVER_GREEN, NODE_REFACTORER, NODE_ARCHITECT):
            return target
        # If no valid target or target is already handled, stay at PAUSE
        return NODE_PAUSE
    
    graph.add_conditional_edges(
        NODE_PAUSE,
        _resume_router,
        {
            NODE_ARCHITECT: NODE_ARCHITECT,
            NODE_DRIVER_RED: NODE_DRIVER_RED,
            NODE_DRIVER_GREEN: NODE_DRIVER_GREEN,
            NODE_REFACTORER: NODE_REFACTORER,
            NODE_PAUSE: NODE_PAUSE,
        },
    )
    
    return graph


def _infer_resume_target(state: RunState) -> str:
    """Infer the resume target node from the last interrupt or rejection.
    
    Priority:
    1. Last interrupt's context.next_phase (for manual checkpoints, budget, etc.)
    2. Last rejection's checkpoint (for same-root-cause trigger)
    3. Current phase
    """
    # Check last interrupt for explicit next_phase
    if state.interrupt_log:
        last_interrupt = state.interrupt_log[-1]
        context = getattr(last_interrupt, 'context', {}) or {}
        if isinstance(context, dict) and context.get("next_phase"):
            return context["next_phase"]
    
    # Check last rejection for checkpoint type
    if state.reviewer_rejection_history:
        last_rejection = state.reviewer_rejection_history[-1]
        return CHECKPOINT_TO_RESUME_NODE.get(last_rejection.checkpoint, NODE_DRIVER_RED)
    
    # Fall back to current phase
    return state.phase


def resume_ticket(
    state: RunState,
    llm_configs: dict[str, Any],
    base_dir: str = ".",
    prompts_dir: str | None = None,
    max_turns: int = 50,
) -> RunState:
    """Resume a paused ticket from its saved state.
    
    This function:
    1. Determines the resume target from interrupt context or rejection history
    2. Sets resume_target and clears AWAITING_HUMAN status
    3. Invokes the graph with the updated state
    
    Args:
        state: The loaded RunState from a paused run
        llm_configs: Per-node LLM configurations
        base_dir: Base directory for file operations
        prompts_dir: Directory containing node system prompts
        max_turns: Maximum number of graph iterations (safety limit)
    
    Returns:
        Final RunState after graph completes or interrupts again
    """
    from langgraph.errors import GraphRecursionError
    
    # Determine where to resume
    resume_target = _infer_resume_target(state)
    
    # Prepare state for resumption
    resume_state = state.model_copy(update={
        "resume_target": resume_target,
        "status": RunStatus.IN_PROGRESS,
        "phase": resume_target,  # Set phase to match target
    })
    
    # Build and compile graph
    graph = build_graph(llm_configs, base_dir, prompts_dir)
    app = graph.compile()
    
    # Run with recursion limit
    try:
        final_state = app.invoke(
            resume_state,
            {"recursion_limit": max_turns},
        )
        return final_state
    except GraphRecursionError:
        return resume_state.model_copy(update={
            "status": RunStatus.BLOCKED,
            "phase": "recursion_limit_exceeded",
        })


def run_ticket(
    ticket_id: str,
    llm_configs: dict[str, Any],
    base_dir: str = ".",
    prompts_dir: str | None = None,
    max_turns: int = 50,
) -> RunState:
    """Run a ticket through the graph from start to finish (or interrupt).
    
    This is a convenience function that:
    1. Creates initial state
    2. Builds the graph
    3. Runs the graph
    4. Returns final state
    
    Args:
        ticket_id: The ticket ID to run
        llm_configs: Per-node LLM configurations
        base_dir: Base directory for file operations
        prompts_dir: Directory containing node system prompts
        max_turns: Maximum number of graph iterations (safety limit)
    
    Returns:
        Final RunState after graph completes or interrupts
    """
    from battalion.state.models import Budget, RunStatus
    from langgraph.errors import GraphRecursionError
    
    # Create initial state
    initial_state = RunState(
        schema_version="1.0",
        run_id=f"run-{ticket_id}",
        ticket_id=ticket_id,
        status=RunStatus.NOT_STARTED,
        phase=NODE_ARCHITECT,
        write_scope={
            "architect": ["plan.md"],
            "driver": ["src/"],
            "reviewer": [],
        },
        retry_bound=2,
        budget=Budget(limit=100, used=0),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=[],
    )
    
    # Build graph
    graph = build_graph(llm_configs, base_dir, prompts_dir)
    
    # Compile graph
    app = graph.compile()
    
    # Run with recursion limit
    try:
        final_state = app.invoke(
            initial_state,
            {"recursion_limit": max_turns},
        )
        return final_state
    except GraphRecursionError:
        # Graph hit max turns - this is a safety limit, not an error
        # Return the last state with a note
        # In practice, this shouldn't happen with reasonable max_turns
        return initial_state.model_copy(update={
            "status": RunStatus.BLOCKED,
            "phase": "recursion_limit_exceeded",
        })
