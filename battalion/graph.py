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

from pathlib import Path
from typing import Any, Callable

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from battalion.interrupts.budget import increment_budget
from battalion.context import (
    architect_context,
    driver_context,
    refactorer_context,
    reviewer_context,
)
from battalion.intel.models import AcceptedInstinct, InstinctAudience
from battalion.intel.repository import IntelRepository
from battalion.intel.retrieval import InstinctRetriever
from battalion.interrupts.triggers import (
    TRIGGER_SAME_ROOT_CAUSE,
    check_any_trigger,
    log_interrupt,
)
from battalion.llm.litellm_client import InfraFailure
from battalion.execution import ExecutionCapture
from battalion.scope.tool_binding import ScopeViolationError
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


def _role_instincts(
    retriever: InstinctRetriever,
    state: RunState,
    role: InstinctAudience,
) -> tuple[AcceptedInstinct, ...]:
    task_text = f"{state.ticket_id}\n{state.spec}"
    return retriever.retrieve(role, task_text).selected

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

_CHECKPOINT_TO_NODE = {checkpoint: node for node, checkpoint in NODE_TO_CHECKPOINT.items()}


def _reviewer_node_name(checkpoint: CheckpointType) -> str:
    """Map a checkpoint type to its graph node name."""
    return _CHECKPOINT_TO_NODE[checkpoint]

# Resume target mapping from checkpoint type to node name
CHECKPOINT_TO_RESUME_NODE = {
    CheckpointType.RED_CHECK: NODE_DRIVER_RED,
    CheckpointType.GREEN_CHECK: NODE_DRIVER_GREEN,
    CheckpointType.REFACTOR_CHECK: NODE_REFACTORER,
}

# Concrete (unambiguous) successor node for each of the four nodes whose
# outgoing edge is a *fixed* topology edge (not accept/reject-conditional).
# Used to populate interrupt context with a real resume target — PHASE_TO_NODE
# above is too lossy for this (e.g. both driver_red and driver_green report
# their next phase as the generic "reviewer", which PHASE_TO_NODE always
# resolves back to NODE_REVIEWER_RED regardless of which mode actually ran).
NEXT_NODE_ON_PAUSE = {
    NODE_ARCHITECT: NODE_DRIVER_RED,
    NODE_DRIVER_RED: NODE_REVIEWER_RED,
    NODE_DRIVER_GREEN: NODE_REVIEWER_GREEN,
    NODE_REFACTORER: NODE_REVIEWER_REFACTOR,
}


def _configured_model(llm_configs: dict[str, Any], role: str) -> str:
    config = llm_configs.get(role, llm_configs.get("default"))
    return getattr(config, "model", "unconfigured")


def _handle_node_error(
    state: RunState,
    error: Exception,
    next_phase: str,
    resume_node: str,
    node_name: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
) -> RunState:
    """Route a node-level exception to its interrupt trigger and pause.

    InfraFailure (LLM call failed after retries, trigger #5) and
    ScopeViolationError (out-of-scope write attempt, trigger #2) must
    surface as an AWAITING_HUMAN pause with an interrupt logged — not crash
    the whole invoke() with an unhandled exception (spec.md AC: "surfaces
    as a distinct failure state, not an unhandled exception"). The pause
    records resume_node so a later `battalion resume` continues where the
    interrupted node would have handed off. Any other exception type is
    re-raised unchanged — it's a bug, not a trigger.

    If on_node_event is given, an "interrupt" event is emitted for the
    paused run so the CLI can tell the human what happened.
    """
    should_pause, trigger_id, context = check_any_trigger(
        state, error=error, next_phase=next_phase
    )
    if should_pause:
        context = {**context, "next_phase": resume_node}
        new_state = log_interrupt(state, trigger_id, context)
        paused = new_state.model_copy(update={"phase": NODE_PAUSE})
        if on_node_event is not None:
            on_node_event({
                "type": "interrupt",
                "node": node_name,
                "trigger": trigger_id,
                "context": context,
            })
        return paused
    raise error


def _make_architect_node(
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
) -> Callable[[RunState], RunState]:
    """Create the Architect node function for the graph."""
    from battalion.nodes.architect import run_architect
    from battalion.prompts.loader import load_system_prompt
    
    def node(state: RunState) -> RunState:
        entered_state = state
        capture = ExecutionCapture.start(
            state, NODE_ARCHITECT, _configured_model(llm_configs, "architect"), base_dir
        )
        # Increment budget for this LLM call
        state = increment_budget(state)
        
        # Run Architect node
        instincts = (
            _role_instincts(instinct_retriever, state, InstinctAudience.ARCHITECT)
            if instinct_retriever is not None
            else ()
        )
        spec_text = architect_context(state, instincts=instincts)
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_start",
                "node": NODE_ARCHITECT,
                "budget": {"used": state.budget.used, "limit": state.budget.limit},
            })
        try:
            node_kwargs = {"on_stream": on_token} if on_token is not None else {}
            new_state = run_architect(
                state=state,
                spec_text=spec_text,
                llm_config=llm_configs.get("architect", llm_configs.get("default")),
                base_dir=base_dir,
                prompts_dir=prompts_dir,
                **node_kwargs,
            )
        except (InfraFailure, ScopeViolationError) as exc:
            if on_node_event is not None:
                on_node_event({
                    "type": "node_error",
                    "node": NODE_ARCHITECT,
                    "error": str(exc),
                })
            paused = _handle_node_error(
                state, exc,
                next_phase=NODE_TO_PHASE[NODE_ARCHITECT],
                resume_node=NEXT_NODE_ON_PAUSE[NODE_ARCHITECT],
                node_name=NODE_ARCHITECT,
                on_node_event=on_node_event,
            )
            return capture.finish(entered_state, paused)
        
        # Check interrupts after node execution
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=NODE_TO_PHASE[NODE_ARCHITECT]
        )
        
        if should_pause:
            context = {**context, "next_phase": NEXT_NODE_ON_PAUSE[NODE_ARCHITECT]}
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
            if on_node_event is not None:
                on_node_event({
                    "type": "interrupt",
                    "node": NODE_ARCHITECT,
                    "trigger": trigger_id,
                    "context": context,
                })
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_end",
                "node": NODE_ARCHITECT,
                "phase": new_state.phase,
                "budget": {"used": new_state.budget.used, "limit": new_state.budget.limit},
            })
        return capture.finish(entered_state, new_state)
    
    return node


def _make_driver_node(
    mode: str,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
) -> Callable[[RunState], RunState]:
    """Create a Driver node function for the graph.
    
    Args:
        mode: "red" or "green" for BTN-11 RED/GREEN mode support
    """
    from battalion.nodes.driver import run_driver
    from battalion.prompts.loader import load_system_prompt
    
    node_name = NODE_DRIVER_RED if mode == "red" else NODE_DRIVER_GREEN
    
    def node(state: RunState) -> RunState:
        entered_state = state
        capture = ExecutionCapture.start(
            state, node_name, _configured_model(llm_configs, "driver"), base_dir
        )
        # Increment budget for this LLM call
        state = increment_budget(state)
        
        instincts = (
            _role_instincts(instinct_retriever, state, InstinctAudience.DRIVER)
            if instinct_retriever is not None
            else ()
        )
        ticket_text = driver_context(state, base_dir, mode, instincts=instincts)
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_start",
                "node": node_name,
                "budget": {"used": state.budget.used, "limit": state.budget.limit},
            })
        try:
            node_kwargs = {"on_stream": on_token} if on_token is not None else {}
            new_state = run_driver(
                state=state,
                ticket_text=ticket_text,
                llm_config=llm_configs.get("driver", llm_configs.get("default")),
                base_dir=base_dir,
                mode=mode,
                prompts_dir=prompts_dir,
                **node_kwargs,
            )
        except (InfraFailure, ScopeViolationError) as exc:
            if on_node_event is not None:
                on_node_event({
                    "type": "node_error",
                    "node": node_name,
                    "error": str(exc),
                })
            paused = _handle_node_error(
                state, exc,
                next_phase=NODE_TO_PHASE.get(f"driver_{mode}", "reviewer"),
                resume_node=node_name,
                node_name=node_name,
                on_node_event=on_node_event,
            )
            return capture.finish(entered_state, paused)
        
        # Driver always transitions to reviewer
        # The specific reviewer checkpoint is determined by the graph edges
        next_phase = NODE_TO_PHASE.get(f"driver_{mode}", "reviewer")
        
        # Check interrupts
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=next_phase
        )
        
        if should_pause:
            context = {**context, "next_phase": NEXT_NODE_ON_PAUSE[node_name]}
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
            if on_node_event is not None:
                on_node_event({
                    "type": "interrupt",
                    "node": node_name,
                    "trigger": trigger_id,
                    "context": context,
                })
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_end",
                "node": node_name,
                "phase": new_state.phase,
                "budget": {"used": new_state.budget.used, "limit": new_state.budget.limit},
            })
        return capture.finish(entered_state, new_state)
    
    return node


def _make_reviewer_node(
    checkpoint: CheckpointType,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
) -> Callable[[RunState], RunState]:
    """Create a Reviewer node function for the graph.
    
    Args:
        checkpoint: The checkpoint type (RED_CHECK, GREEN_CHECK, REFACTOR_CHECK)
    """
    from battalion.nodes.reviewer import run_reviewer
    from battalion.prompts.loader import load_system_prompt
    
    node_name = _reviewer_node_name(checkpoint)
    
    def node(state: RunState) -> RunState:
        entered_state = state
        capture = ExecutionCapture.start(
            state, node_name, _configured_model(llm_configs, "reviewer"), base_dir
        )
        # Reviewer doesn't call LLM for the actual test run, but does for
        # rejection cause articulation. Budget increment for the LLM call.
        state = increment_budget(state)
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_start",
                "node": node_name,
                "budget": {"used": state.budget.used, "limit": state.budget.limit},
            })
        try:
            node_kwargs = {"on_stream": on_token} if on_token is not None else {}
            if instinct_retriever is not None:
                instincts = _role_instincts(
                    instinct_retriever, state, InstinctAudience.REVIEWER
                )
                if instincts:
                    node_kwargs["instinct_context"] = reviewer_context(
                        state, instincts=instincts
                    )
            new_state = run_reviewer(
                state=state,
                base_dir=base_dir,
                llm_config=llm_configs.get("reviewer", llm_configs.get("default")),
                checkpoint=checkpoint,
                prompts_dir=prompts_dir,
                **node_kwargs,
            )
        except (InfraFailure, ScopeViolationError) as exc:
            if on_node_event is not None:
                on_node_event({
                    "type": "node_error",
                    "node": node_name,
                    "error": str(exc),
                })
            paused = _handle_node_error(
                state, exc,
                next_phase=state.phase,
                resume_node=CHECKPOINT_TO_RESUME_NODE[checkpoint],
                node_name=node_name,
                on_node_event=on_node_event,
            )
            return capture.finish(entered_state, paused, checkpoint=checkpoint)
        
        # Reviewer sets the next phase based on accept/reject
        # But we need to check interrupts first
        next_phase = new_state.phase
        
        # Check interrupts
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=next_phase
        )
        
        if should_pause:
            context = {**context, "next_phase": CHECKPOINT_TO_RESUME_NODE[checkpoint]}
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
            if on_node_event is not None:
                on_node_event({
                    "type": "interrupt",
                    "node": node_name,
                    "trigger": trigger_id,
                    "context": context,
                })
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_end",
                "node": node_name,
                "phase": new_state.phase,
                "budget": {"used": new_state.budget.used, "limit": new_state.budget.limit},
            })
        return capture.finish(entered_state, new_state, checkpoint=checkpoint)
    
    return node


def _make_refactorer_node(
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
) -> Callable[[RunState], RunState]:
    """Create the Refactorer node function for the graph."""
    from battalion.nodes.refactorer import run_refactorer
    from battalion.prompts.loader import load_system_prompt
    
    def node(state: RunState) -> RunState:
        entered_state = state
        capture = ExecutionCapture.start(
            state, NODE_REFACTORER,
            _configured_model(llm_configs, "refactorer")
            if "refactorer" in llm_configs
            else _configured_model(llm_configs, "driver"),
            base_dir,
        )
        # Increment budget for this LLM call
        state = increment_budget(state)
        
        instincts = (
            _role_instincts(instinct_retriever, state, InstinctAudience.REFACTORER)
            if instinct_retriever is not None
            else ()
        )
        refactor_text = refactorer_context(state, base_dir, instincts=instincts)
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_start",
                "node": NODE_REFACTORER,
                "budget": {"used": state.budget.used, "limit": state.budget.limit},
            })
        try:
            node_kwargs = {"on_stream": on_token} if on_token is not None else {}
            new_state = run_refactorer(
                state=state,
                refactor_text=refactor_text,
                llm_config=llm_configs.get("refactorer", llm_configs.get("driver", llm_configs.get("default"))),
                base_dir=base_dir,
                prompts_dir=prompts_dir,
                **node_kwargs,
            )
        except (InfraFailure, ScopeViolationError) as exc:
            if on_node_event is not None:
                on_node_event({
                    "type": "node_error",
                    "node": NODE_REFACTORER,
                    "error": str(exc),
                })
            paused = _handle_node_error(
                state, exc,
                next_phase=NODE_TO_PHASE[NODE_REFACTORER],
                resume_node=NEXT_NODE_ON_PAUSE[NODE_REFACTORER],
                node_name=NODE_REFACTORER,
                on_node_event=on_node_event,
            )
            return capture.finish(entered_state, paused)
        
        # Check interrupts
        next_phase = NODE_TO_PHASE[NODE_REFACTORER]
        should_pause, trigger_id, context = check_any_trigger(
            new_state, old_state=state, next_phase=next_phase
        )
        
        if should_pause:
            context = {**context, "next_phase": NEXT_NODE_ON_PAUSE[NODE_REFACTORER]}
            new_state = log_interrupt(new_state, trigger_id, context)
            new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
            if on_node_event is not None:
                on_node_event({
                    "type": "interrupt",
                    "node": NODE_REFACTORER,
                    "trigger": trigger_id,
                    "context": context,
                })
        
        if on_node_event is not None:
            on_node_event({
                "type": "node_end",
                "node": NODE_REFACTORER,
                "phase": new_state.phase,
                "budget": {"used": new_state.budget.used, "limit": new_state.budget.limit},
            })
        return capture.finish(entered_state, new_state)
    
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
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    intel_repository: IntelRepository | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
) -> StateGraph:
    """Build the Battalion StateGraph with all nodes and edges.
    
    The graph implements the RED -> Reviewer -> GREEN -> Reviewer -> 
    Refactorer -> Reviewer loop from plan.md ADR-006 through ADR-009.
    
    Args:
        llm_configs: Per-node LLM configurations (keys: "architect", "driver", 
                     "reviewer", "refactorer", or "default")
        base_dir: Base directory for file operations
        prompts_dir: Directory containing node system prompts
        on_node_event: Optional callback receiving lifecycle event dicts
                      ("node_start", "node_end", "interrupt", "node_error")
                      as they occur during the run.
        on_token: Optional callback receiving streamed LLM token event dicts
                  ({"type": "token"|"reasoning", "content": ...}). Forwarded
                  to each node's LLM call, which uses it only when the
                  provider streams.
        intel_repository: Optional accepted-Instinct repository. Defaults to
                          ``<base_dir>/.battalion/intel``.
        on_state_checkpoint: Optional durable-state callback invoked after each
                             graph node has completed.
    
    Returns:
        Configured StateGraph ready to run
    """
    graph = StateGraph(RunState)
    repository = intel_repository or IntelRepository(
        Path(base_dir) / ".battalion" / "intel"
    )
    instinct_retriever = InstinctRetriever(repository)
    
    # --- Create node functions ---
    shared = {
        "on_node_event": on_node_event,
        "on_token": on_token,
        "instinct_retriever": instinct_retriever,
    }
    architect_node = _make_architect_node(llm_configs, base_dir, prompts_dir, **shared)
    driver_red_node = _make_driver_node("red", llm_configs, base_dir, prompts_dir, **shared)
    driver_green_node = _make_driver_node("green", llm_configs, base_dir, prompts_dir, **shared)
    reviewer_red_node = _make_reviewer_node(
        CheckpointType.RED_CHECK, llm_configs, base_dir, prompts_dir, **shared
    )
    reviewer_green_node = _make_reviewer_node(
        CheckpointType.GREEN_CHECK, llm_configs, base_dir, prompts_dir, **shared
    )
    refactorer_node = _make_refactorer_node(llm_configs, base_dir, prompts_dir, **shared)
    reviewer_refactor_node = _make_reviewer_node(
        CheckpointType.REFACTOR_CHECK, llm_configs, base_dir, prompts_dir, **shared
    )
    done_node = _make_done_node()
    pause_node = _make_pause_node()

    def checkpointed(
        node: Callable[[RunState], RunState],
    ) -> Callable[[RunState], RunState]:
        if on_state_checkpoint is None:
            return node

        def wrapped(state: RunState) -> RunState:
            result = RunState.model_validate(node(state))
            on_state_checkpoint(result)
            return result

        return wrapped

    architect_node = checkpointed(architect_node)
    driver_red_node = checkpointed(driver_red_node)
    driver_green_node = checkpointed(driver_green_node)
    reviewer_red_node = checkpointed(reviewer_red_node)
    reviewer_green_node = checkpointed(reviewer_green_node)
    refactorer_node = checkpointed(refactorer_node)
    reviewer_refactor_node = checkpointed(reviewer_refactor_node)
    done_node = checkpointed(done_node)
    pause_node = checkpointed(pause_node)
    
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
    #
    # Every edge below is gated on state.status: if an interrupt fired during
    # the node that just ran (status == AWAITING_HUMAN), route to NODE_PAUSE
    # instead of the normal next step. Without this gate, an interrupt fired
    # during Architect/Driver/Refactorer was silently ignored — the graph
    # would proceed to the next node anyway, defeating the entire point of
    # human-controlled interrupt points. (Reviewer nodes route on their own
    # accept/reject verdict below, and also check this gate first.)

    def _pause_gate(next_node: str):
        def gate(state: RunState) -> str:
            if state.status == RunStatus.AWAITING_HUMAN:
                return NODE_PAUSE
            return next_node
        return gate

    # Architect -> Driver(RED)
    graph.add_conditional_edges(
        NODE_ARCHITECT, _pause_gate(NODE_DRIVER_RED), [NODE_DRIVER_RED, NODE_PAUSE]
    )

    # Driver(RED) -> Reviewer(RED_CHECK)
    graph.add_conditional_edges(
        NODE_DRIVER_RED, _pause_gate(NODE_REVIEWER_RED), [NODE_REVIEWER_RED, NODE_PAUSE]
    )

    # Reviewer(RED_CHECK) conditional edges.
    # accept -> phase="driver_green" -> Driver(GREEN)
    # reject -> phase="driver_red" -> retry Driver(RED)
    # These two phase values used to both be the generic "driver", which made
    # accept and reject indistinguishable — a rejected RED check was silently
    # routed to Driver(GREEN) as if it had passed. They're now distinct.
    # The previous version of this edge set also had a *second*, unconditional
    # add_edge(NODE_REVIEWER_RED, NODE_DRIVER_RED) alongside this conditional
    # edge — LangGraph fired both in the same step whenever the conditional
    # picked a different target, causing a guaranteed InvalidUpdateError
    # crash the first time any Reviewer checkpoint completed. Removed.
    graph.add_conditional_edges(
        NODE_REVIEWER_RED,
        lambda state: (
            NODE_PAUSE if state.status == RunStatus.AWAITING_HUMAN
            else NODE_DRIVER_GREEN if state.phase == "driver_green"
            else NODE_DRIVER_RED
        ),
        [NODE_DRIVER_GREEN, NODE_DRIVER_RED, NODE_PAUSE],
    )

    # Driver(GREEN) -> Reviewer(GREEN_CHECK)
    graph.add_conditional_edges(
        NODE_DRIVER_GREEN, _pause_gate(NODE_REVIEWER_GREEN), [NODE_REVIEWER_GREEN, NODE_PAUSE]
    )

    # Reviewer(GREEN_CHECK) conditional edges.
    # If accepted (tests pass): -> Refactorer
    # If rejected (tests fail): -> Driver(GREEN) to retry
    # The AWAITING_HUMAN check must come first here too — previously an
    # interrupt fired inside this Reviewer node (e.g. budget exceeded while
    # articulating a rejection cause) fell through the "else" branch straight
    # into a Driver(GREEN) retry instead of pausing.
    graph.add_conditional_edges(
        NODE_REVIEWER_GREEN,
        lambda state: (
            NODE_PAUSE if state.status == RunStatus.AWAITING_HUMAN
            else NODE_REFACTORER if state.phase == "refactorer"
            else NODE_DRIVER_GREEN
        ),
        [NODE_REFACTORER, NODE_DRIVER_GREEN, NODE_PAUSE],
    )

    # Refactorer -> Reviewer(REFACTOR_CHECK)
    graph.add_conditional_edges(
        NODE_REFACTORER, _pause_gate(NODE_REVIEWER_REFACTOR), [NODE_REVIEWER_REFACTOR, NODE_PAUSE]
    )

    # Reviewer(REFACTOR_CHECK) conditional edges.
    # If accepted (tests still pass): -> DONE
    # If rejected (tests fail): -> Refactorer to retry
    graph.add_conditional_edges(
        NODE_REVIEWER_REFACTOR,
        lambda state: (
            NODE_PAUSE if state.status == RunStatus.AWAITING_HUMAN
            else NODE_DONE if state.phase == "done"
            else NODE_REFACTORER
        ),
        [NODE_DONE, NODE_REFACTORER, NODE_PAUSE],
    )

    # PAUSE is a clean terminal within a single invoke() call: whenever an
    # interrupt fires, execution should stop here and return control to the
    # caller (the CLI), not try to keep routing internally. Resuming later
    # is handled by *where the next invoke() call starts* (see the
    # conditional entry point below), not by PAUSE routing onward — a single
    # invoke() never continues past a real pause.
    graph.add_edge(NODE_PAUSE, END)
    
    # DONE is terminal - use END constant
    graph.add_edge(NODE_DONE, END)
    
    # Entry point: NODE_ARCHITECT for a fresh run, or resume_target's node
    # for a resumed one.
    #
    # This used to be a fixed graph.set_entry_point(NODE_ARCHITECT), which
    # meant resume_ticket's resume_target bookkeeping had no actual effect —
    # app.invoke() always starts at the configured entry point regardless of
    # what's in the state passed in, so every "resume" silently restarted
    # the ticket from Architect instead of continuing where it paused. The
    # conditional entry point below is what actually makes resume_target
    # take effect.
    def _entry_router(state: RunState) -> str:
        target = state.resume_target
        if target in (
            NODE_ARCHITECT,
            NODE_DRIVER_RED,
            NODE_DRIVER_GREEN,
            NODE_REVIEWER_RED,
            NODE_REVIEWER_GREEN,
            NODE_REFACTORER,
            NODE_REVIEWER_REFACTOR,
        ):
            return target
        return NODE_ARCHITECT
    
    graph.set_conditional_entry_point(
        _entry_router,
        {
            NODE_ARCHITECT: NODE_ARCHITECT,
            NODE_DRIVER_RED: NODE_DRIVER_RED,
            NODE_DRIVER_GREEN: NODE_DRIVER_GREEN,
            NODE_REVIEWER_RED: NODE_REVIEWER_RED,
            NODE_REVIEWER_GREEN: NODE_REVIEWER_GREEN,
            NODE_REFACTORER: NODE_REFACTORER,
            NODE_REVIEWER_REFACTOR: NODE_REVIEWER_REFACTOR,
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
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
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
        on_node_event: Optional callback for node lifecycle events
        on_token: Optional callback for streamed LLM token events
        on_state_checkpoint: Optional durable-state callback after each node
    
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
    graph = build_graph(
        llm_configs, base_dir, prompts_dir,
        on_node_event=on_node_event, on_token=on_token,
        on_state_checkpoint=on_state_checkpoint,
    )
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
    initial_state: RunState,
    llm_configs: dict[str, Any],
    base_dir: str = ".",
    prompts_dir: str | None = None,
    max_turns: int = 50,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
) -> RunState:
    """Run a caller-created state through the graph until done or interrupted.
    
    ``initial_state`` is the sole source of truth for run configuration.  The
    function deliberately does not accept duplicate ticket, specification,
    budget, checkpoint, scope, or retry arguments that could conflict with it.
    
    Args:
        initial_state: Complete initial state, including all run configuration
        llm_configs: Per-node LLM configurations
        base_dir: Base directory for file operations
        prompts_dir: Directory containing node system prompts
        max_turns: Maximum number of graph iterations (safety limit)
        on_node_event: Optional callback for node lifecycle events
        on_token: Optional callback for streamed LLM token events
        on_state_checkpoint: Optional durable-state callback after each node
    
    Returns:
        Final RunState after graph completes or interrupts
    """
    from langgraph.errors import GraphRecursionError
    
    # Build graph
    graph = build_graph(
        llm_configs, base_dir, prompts_dir,
        on_node_event=on_node_event, on_token=on_token,
        on_state_checkpoint=on_state_checkpoint,
    )
    
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
