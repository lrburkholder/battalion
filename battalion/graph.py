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
and pauses. Shared application operations authorize resumption for CLI and
desktop clients; the graph owns execution routing.
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
    check_budget_exceeded_trigger,
    log_interrupt,
)
from battalion.llm.litellm_client import InfraFailure, InferenceIdentityContradiction
from battalion.nodes.errors import RoleContractViolation, RoleOutputError
from battalion.execution import ExecutionCapture
from battalion.recovery import UnsafeRecoveryError, assess_recovery
from battalion.scope.tool_binding import ScopeViolationError, validate_write_scope
from battalion.state.models import (
    CheckpointType,
    GraphProgress,
    InterventionDisposition,
    ProgressStage,
    RejectionRecord,
    RoleContractViolationEvidence,
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
NODE_BLOCKED = "blocked"


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


def _successor(state: RunState, node_name: str) -> str:
    """Use the same exact successor for live routing and durable recovery."""
    if state.status is RunStatus.AWAITING_HUMAN:
        return NODE_PAUSE
    if state.status is RunStatus.BLOCKED:
        return NODE_BLOCKED
    if node_name in NEXT_NODE_ON_PAUSE:
        return NEXT_NODE_ON_PAUSE[node_name]
    accepted, rejected = {
        NODE_REVIEWER_RED: (NODE_DRIVER_GREEN, NODE_DRIVER_RED),
        NODE_REVIEWER_GREEN: (NODE_REFACTORER, NODE_DRIVER_GREEN),
        NODE_REVIEWER_REFACTOR: (NODE_DONE, NODE_REFACTORER),
    }[node_name]
    return accepted if state.phase == accepted else rejected


def _deliver_interventions(
    state: RunState,
    target: str,
    execution_id: str,
    on_state_checkpoint: Callable[[RunState], None] | None,
) -> RunState:
    """Associate queued context with one attempt before provider generation."""
    matched = {
        item.action_id for item in state.interventions
        if item.target.value == target
        and item.disposition is InterventionDisposition.QUEUED
    }
    if not matched:
        return state
    if not any(
        item.execution_id == execution_id and item.phase == target
        and item.outcome == "in-progress"
        for item in state.execution_record.node_executions
    ):
        raise ValueError("Intervention delivery requires an existing unfinished target attempt")
    interventions = [
        item.model_copy(update={
            "disposition": InterventionDisposition.DELIVERED,
            "delivered_to_execution_id": execution_id,
        }) if item.action_id in matched else item
        for item in state.interventions
    ]
    actions = [
        item.model_copy(update={
            "disposition": "delivered",
            "resulting_state_version": state.schema_version,
            "resulting_status": state.status,
            "resulting_phase": state.phase,
        }) if item.action_id in matched else item
        for item in state.human_action_log
    ]
    delivered = state.model_copy(update={
        "interventions": interventions,
        "human_action_log": actions,
    })
    if on_state_checkpoint is not None:
        on_state_checkpoint(delivered)
    return delivered


def _handle_node_error(
    state: RunState,
    error: Exception,
    next_phase: str,
    resume_node: str,
    node_name: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
) -> RunState:
    """Route a node-level exception to its interrupt trigger and pause.

    InfraFailure (LLM call failed after retries), RoleOutputError (malformed
    or contract-violating provider output), and
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


def _resolve_llm_config(llm_configs: dict[str, Any], roles: tuple[str, ...]) -> Any:
    """Resolve per-role LLM configuration through nested fallbacks.

    Roles are tried left to right against ``llm_configs``; when none is
    present, the ``"default"`` entry wins. This reproduces the historical
    per-factory chains (for example Refactorer's
    ``get("refactorer", get("driver", get("default")))``) in one place.
    """
    config = llm_configs.get("default")
    for role in roles:
        config = llm_configs.get(role, config)
    return config


def _reported_effective_identities(calls: list[Any]) -> set[str]:
    """Return only concrete identities emitted by an inference response/router."""
    return {
        value.casefold()
        for call in calls
        for value in (getattr(call, "response_model", None), getattr(call, "routed_model", None))
        if isinstance(value, str) and value.strip()
    }


def _runtime_diversity_contradiction(
    state: RunState, node_name: str, capture: ExecutionCapture, model_config: Any,
) -> InferenceIdentityContradiction | None:
    """Reject proven Driver/Reviewer identity collisions without alias guessing."""
    role = "driver" if node_name.startswith("driver") else (
        "reviewer" if node_name.startswith("reviewer") else None
    )
    if role not in {"driver", "reviewer"}:
        return None
    current = _reported_effective_identities(capture.llm_calls)
    if not current:
        return None
    peer = "reviewer" if role == "driver" else "driver"
    prior = _reported_effective_identities([
        call
        for execution in state.execution_record.node_executions
        if execution.role == peer
        for call in execution.llm_calls
    ])
    overlap = current & prior
    if not overlap:
        return None
    identity = sorted(overlap)[0]
    detail = (
        f"Runtime inference evidence resolved both Driver and Reviewer to {identity!r}; "
        "this contradicts the configured model-diversity assumption."
    )
    for call in capture.llm_calls:
        if identity in _reported_effective_identities([call]):
            call.identity_contradiction = detail
    return InferenceIdentityContradiction(
        node_name, getattr(model_config, "model", "unconfigured"), 1, RuntimeError(detail)
    )


def _next_phase_value(
    value: str | Callable[[RunState], str], state: RunState
) -> str:
    return value if isinstance(value, str) else value(state)


def _scaffold_node(
    *,
    node_name: str,
    llm_roles: tuple[str, ...],
    runner: Callable[..., RunState],
    build_inputs: Callable[
        [RunState, tuple[AcceptedInstinct, ...], str, str | None], dict[str, Any]
    ],
    audience: InstinctAudience | None,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None,
    on_node_event: Callable[[dict], None] | None,
    on_token: Callable[[dict], None] | None,
    instinct_retriever: InstinctRetriever | None,
    on_state_checkpoint: Callable[[RunState], None] | None,
    delivers_interventions: bool = True,
    static_kwargs: dict[str, Any] | None = None,
    finish_checkpoint: CheckpointType | None = None,
    error_next_phase: str | Callable[[RunState], str],
    error_resume_node: str,
    check_next_phase: str | Callable[[RunState], str],
    pause_resume_node: str,
) -> Callable[[RunState], RunState]:
    """Build one graph node around the shared per-attempt scaffolding.

    Every role node follows the same sequence: open an execution capture,
    deliver queued human interventions bound to this attempt, increment the
    run budget, assemble deterministic bounded context, emit node_start,
    call the role runner, then either pause on an interrupt trigger or hand
    off to the next phase. The per-role differences are exactly the
    parameters above:

      runner:                 node implementation (run_architect, ...)
      build_inputs:           dynamic runner kwargs from state/instincts/attempt
      audience:               Instinct retrieval audience; None disables it
      delivers_interventions: whether queued interventions may target this node
      static_kwargs:          fixed runner kwargs (Driver mode, Reviewer
                              checkpoint)
      finish_checkpoint:      checkpoint recorded on Reviewer executions
      error_/check_/pause_resume_node: routing values for the failure,
                              interrupt-check, and paused-resume paths

    Recoverable RoleContractViolation instances receive one automatic retry in
    the same role and phase. InfraFailure and the remaining RoleOutputError
    instances (trigger #5) and ScopeViolationError
    (trigger #2) route to an AWAITING_HUMAN pause through _handle_node_error;
    invalid scope configuration fails before the attempt; unexpected exceptions
    propagate unchanged.
    """
    def node(state: RunState) -> RunState:
        # Reject authority configuration before snapshots, model calls, budget
        # consumption, or durable attempts. Node binding rechecks before use.
        validate_write_scope(state.write_scope, base_dir)
        recovery = assess_recovery(state)
        if recovery is not None and recovery.disposition == "terminal":
            raise UnsafeRecoveryError(recovery.message)
        progress = state.graph_progress
        correction_context = progress.correction_context if progress else None
        correction_attempt = progress.correction_attempt if progress else 0

        def finish(
            result: RunState, *, violation: RoleContractViolationEvidence | None = None,
            correction: str | None = None,
        ) -> RunState:
            result = capture.finish(
                entered_state, result, checkpoint=finish_checkpoint,
                role_contract_violation=violation,
            )
            result = result.model_copy(update={
                "graph_progress": GraphProgress(
                    stage=ProgressStage.ATTEMPT_COMPLETED,
                    execution_id=capture.execution_id,
                    next_node=node_name if correction else _successor(result, node_name),
                    correction_context=correction,
                    correction_attempt=1 if correction else 0,
                ),
                "resume_intent": (
                    result.resume_intent.model_copy(update={"completed": True})
                    if result.resume_intent else None
                ),
            })
            if on_state_checkpoint is not None:
                on_state_checkpoint(result)
            return result

        while True:
            # Corrections share the Run budget. Check before creating another
            # attempt, including recovery from the saved rejected candidate.
            # A pending resume intent is the human's explicit continuation;
            # finish() consumes it when that authorized attempt completes.
            budget_exhausted, budget_trigger = check_budget_exceeded_trigger(state)
            if (correction_context is not None and budget_exhausted
                    and not (state.resume_intent and not state.resume_intent.completed)):
                context = {
                    "used": state.budget.used, "limit": state.budget.limit,
                    "next_phase": node_name,
                }
                state = log_interrupt(state, budget_trigger, context).model_copy(update={
                    "phase": NODE_PAUSE,
                })
                if on_state_checkpoint is not None:
                    on_state_checkpoint(state)
                if on_node_event is not None:
                    on_node_event({
                        "type": "interrupt", "node": node_name,
                        "trigger": budget_trigger, "context": context,
                    })
                return state
            entered_state = state
            model_config = _resolve_llm_config(llm_configs, llm_roles)
            capture = ExecutionCapture.start(
                state, node_name, getattr(model_config, "model", "unconfigured"),
                base_dir, prompts_dir=prompts_dir, model_configuration=model_config,
            )
            progress = state.graph_progress
            if progress and progress.stage is ProgressStage.ATTEMPT_CREATED:
                # Generation has not begun. Reuse the durable receiving attempt.
                previous = next(
                    item for item in state.execution_record.node_executions
                    if item.execution_id == progress.execution_id
                )
                if previous.phase != node_name or previous.outcome != "in-progress":
                    raise UnsafeRecoveryError("Attempt recovery identity does not match the target")
                if (previous.model_identity != capture.model_identity
                        or previous.prompt_provenance != capture.prompt_provenance):
                    raise UnsafeRecoveryError(
                        "Restore the original model and prompt configuration before retrying "
                        "this unstarted attempt, or start a new run."
                    )
                capture.execution_id = previous.execution_id
                capture.started_at = previous.started_at
            else:
                state = capture.create_attempt(state)
            state = state.model_copy(update={
                "graph_progress": GraphProgress(
                    stage=ProgressStage.ATTEMPT_CREATED, next_node=node_name,
                    execution_id=capture.execution_id,
                    correction_context=correction_context,
                    correction_attempt=correction_attempt,
                ),
            })
            if delivers_interventions:
                state = _deliver_interventions(
                    state, node_name, capture.execution_id, None
                )
                capture.include_human_interventions(state)
            # Creation and delivery are one atomic checkpoint before role execution.
            if on_state_checkpoint is not None:
                on_state_checkpoint(state)
            # Increment budget for every model call, including corrections.
            state = increment_budget(state)

            instincts = (
                _role_instincts(instinct_retriever, state, audience)
                if instinct_retriever is not None and audience is not None
                else ()
            )
            inputs = build_inputs(
                state, instincts, capture.execution_id, correction_context
            )

            state = state.model_copy(update={
                "graph_progress": state.graph_progress.model_copy(update={
                    "stage": ProgressStage.ATTEMPT_STARTED,
                }),
            })
            if on_state_checkpoint is not None:
                on_state_checkpoint(state)
            if on_node_event is not None:
                on_node_event({
                    "type": "node_start",
                    "node": node_name,
                    "budget": {"used": state.budget.used, "limit": state.budget.limit},
                })
            try:
                # on_stream is passed only when a token callback exists, matching
                # the historical per-factory call shape (mocked runners in tests
                # bind narrow signatures).
                call_kwargs: dict[str, Any] = {
                    "state": state,
                    "llm_config": model_config,
                    "base_dir": base_dir,
                    "prompts_dir": prompts_dir,
                    **(static_kwargs or {}),
                    **inputs,
                }
                if on_token is not None:
                    call_kwargs["on_stream"] = on_token
                new_state = runner(**call_kwargs)
                contradiction = _runtime_diversity_contradiction(
                    state, node_name, capture, model_config
                )
                if contradiction is not None:
                    raise contradiction
            except RoleContractViolation as exc:
                correction_attempt += 1
                evidence = RoleContractViolationEvidence(
                    reason_code=exc.reason_code,
                    detail=str(exc),
                    offending_paths=list(exc.offending_paths),
                    attempt_number=correction_attempt,
                    resulting_disposition=(
                        "retry" if correction_attempt == 1 else "escalation"
                    ),
                )
                if correction_attempt == 1:
                    # Make a process interruption between correction attempts
                    # resumable at this exact role rather than restarting the
                    # graph from Architect. The durable record is already
                    # complete and no candidate mutation was applied.
                    state = state.model_copy(update={
                        "phase": node_name,
                        "resume_target": node_name,
                    })
                    correction_context = exc.correction_context()
                    state = finish(state, violation=evidence, correction=correction_context)
                    if on_node_event is not None:
                        on_node_event({
                            "type": "role_contract_correction",
                            "node": node_name,
                            "reason_code": exc.reason_code,
                            "offending_paths": list(exc.offending_paths),
                            "mutation_applied": False,
                            "attempt_number": correction_attempt,
                        })
                    continue

                if on_node_event is not None:
                    on_node_event({
                        "type": "node_error",
                        "node": node_name,
                        "error": str(exc),
                    })
                paused = _handle_node_error(
                    state, exc,
                    next_phase=_next_phase_value(error_next_phase, state),
                    resume_node=error_resume_node,
                    node_name=node_name,
                    on_node_event=on_node_event,
                )
                return finish(paused, violation=evidence)
            except (InfraFailure, RoleOutputError, ScopeViolationError) as exc:
                if on_node_event is not None:
                    on_node_event({
                        "type": "node_error",
                        "node": node_name,
                        "error": str(exc),
                    })
                paused = _handle_node_error(
                    state, exc,
                    next_phase=_next_phase_value(error_next_phase, state),
                    resume_node=error_resume_node,
                    node_name=node_name,
                    on_node_event=on_node_event,
                )
                return finish(paused)

            # A valid typed blocked/escalated role result owns its routing.
            # Do not reinterpret it as a normal success edge or let unrelated
            # post-success triggers overwrite its durable state.
            if new_state.status in {RunStatus.AWAITING_HUMAN, RunStatus.BLOCKED}:
                new_state = finish(new_state)
                if on_node_event is not None:
                    on_node_event({
                        "type": "node_end",
                        "node": node_name,
                        "phase": new_state.phase,
                        "budget": {
                            "used": new_state.budget.used,
                            "limit": new_state.budget.limit,
                        },
                    })
                return new_state

            # Check interrupts after successful node execution.
            should_pause, trigger_id, context = check_any_trigger(
                new_state, old_state=state,
                next_phase=_next_phase_value(check_next_phase, new_state),
            )
            if should_pause:
                context = {**context, "next_phase": pause_resume_node}
                new_state = log_interrupt(new_state, trigger_id, context)
                new_state = new_state.model_copy(update={"phase": NODE_PAUSE})
                new_state = finish(new_state)
                if on_node_event is not None:
                    on_node_event({
                        "type": "interrupt",
                        "node": node_name,
                        "trigger": trigger_id,
                        "context": context,
                    })

            else:
                new_state = finish(new_state)
            if on_node_event is not None:
                on_node_event({
                    "type": "node_end",
                    "node": node_name,
                    "phase": new_state.phase,
                    "budget": {"used": new_state.budget.used, "limit": new_state.budget.limit},
                })
            return new_state

    return node


def _make_architect_node(
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
) -> Callable[[RunState], RunState]:
    """Create the Architect node function for the graph."""
    from battalion.nodes.architect import run_architect

    def build_inputs(
        state: RunState,
        instincts: tuple[AcceptedInstinct, ...],
        execution_id: str,
        correction_context: str | None,
    ) -> dict[str, Any]:
        return {
            "spec_text": architect_context(
                state, instincts=instincts, node_execution_id=execution_id,
                automatic_correction=correction_context,
            )
        }

    return _scaffold_node(
        node_name=NODE_ARCHITECT,
        llm_roles=("architect",),
        runner=run_architect,
        build_inputs=build_inputs,
        audience=InstinctAudience.ARCHITECT,
        llm_configs=llm_configs,
        base_dir=base_dir,
        prompts_dir=prompts_dir,
        on_node_event=on_node_event,
        on_token=on_token,
        instinct_retriever=instinct_retriever,
        on_state_checkpoint=on_state_checkpoint,
        error_next_phase=NODE_TO_PHASE[NODE_ARCHITECT],
        # A failed Architect attempt did not produce an approved plan, so a
        # human-authorized retry must return to Architect rather than skip to
        # Driver with stale or absent design context.
        error_resume_node=NODE_ARCHITECT,
        check_next_phase=NODE_TO_PHASE[NODE_ARCHITECT],
        pause_resume_node=NEXT_NODE_ON_PAUSE[NODE_ARCHITECT],
    )


def _make_driver_node(
    mode: str,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
) -> Callable[[RunState], RunState]:
    """Create a Driver node function for the graph.

    Args:
        mode: "red" or "green" for BTN-11 RED/GREEN mode support
    """
    from battalion.nodes.driver import run_driver

    node_name = NODE_DRIVER_RED if mode == "red" else NODE_DRIVER_GREEN

    def build_inputs(
        state: RunState,
        instincts: tuple[AcceptedInstinct, ...],
        execution_id: str,
        correction_context: str | None,
    ) -> dict[str, Any]:
        return {
            "ticket_text": driver_context(
                state, base_dir, mode,
                instincts=instincts, node_execution_id=execution_id,
                automatic_correction=correction_context,
            )
        }

    driver_next_phase = NODE_TO_PHASE.get(f"driver_{mode}", "reviewer")
    return _scaffold_node(
        node_name=node_name,
        llm_roles=("driver",),
        runner=run_driver,
        build_inputs=build_inputs,
        audience=InstinctAudience.DRIVER,
        static_kwargs={"mode": mode},
        llm_configs=llm_configs,
        base_dir=base_dir,
        prompts_dir=prompts_dir,
        on_node_event=on_node_event,
        on_token=on_token,
        instinct_retriever=instinct_retriever,
        on_state_checkpoint=on_state_checkpoint,
        # A failed Driver attempt resumes at itself so the retry re-runs the
        # same RED/GREEN phase rather than skipping ahead.
        error_next_phase=driver_next_phase,
        error_resume_node=node_name,
        check_next_phase=driver_next_phase,
        pause_resume_node=NEXT_NODE_ON_PAUSE[node_name],
    )


def _make_reviewer_node(
    checkpoint: CheckpointType,
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
    reviewer_test_timeout_seconds: float = 300.0,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
) -> Callable[[RunState], RunState]:
    """Create a Reviewer node function for the graph.

    Args:
        checkpoint: The checkpoint type (RED_CHECK, GREEN_CHECK, REFACTOR_CHECK)

    Reviewer runs tests mechanically and only calls the LLM to articulate a
    rejection cause. It never receives write tools, queued interventions do
    not target it, and its verdict evidence is recorded through the
    checkpoint handed to the execution-capture finish path.
    """
    from battalion.nodes.reviewer import run_reviewer

    node_name = _reviewer_node_name(checkpoint)

    def build_inputs(
        state: RunState,
        instincts: tuple[AcceptedInstinct, ...],
        execution_id: str,
        _correction_context: str | None,
    ) -> dict[str, Any]:
        if instincts:
            return {"instinct_context": reviewer_context(state, instincts=instincts)}
        return {}

    resume_node = CHECKPOINT_TO_RESUME_NODE[checkpoint]
    return _scaffold_node(
        node_name=node_name,
        llm_roles=("reviewer",),
        runner=run_reviewer,
        build_inputs=build_inputs,
        audience=InstinctAudience.REVIEWER,
        delivers_interventions=False,
        static_kwargs={
            "checkpoint": checkpoint,
            "test_timeout_seconds": reviewer_test_timeout_seconds,
        },
        finish_checkpoint=checkpoint,
        llm_configs=llm_configs,
        base_dir=base_dir,
        prompts_dir=prompts_dir,
        on_node_event=on_node_event,
        on_token=on_token,
        instinct_retriever=instinct_retriever,
        on_state_checkpoint=on_state_checkpoint,
        error_next_phase=lambda state: state.phase,
        # A failed review has no verdict to hand off; re-run this exact
        # checkpoint after the operator resolves the provider problem.
        error_resume_node=node_name,
        check_next_phase=lambda state: state.phase,
        pause_resume_node=resume_node,
    )


def _make_refactorer_node(
    llm_configs: dict[str, Any],
    base_dir: str,
    prompts_dir: str | None = None,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    instinct_retriever: InstinctRetriever | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
) -> Callable[[RunState], RunState]:
    """Create the Refactorer node function for the graph."""
    from battalion.nodes.refactorer import run_refactorer

    def build_inputs(
        state: RunState,
        instincts: tuple[AcceptedInstinct, ...],
        execution_id: str,
        _correction_context: str | None,
    ) -> dict[str, Any]:
        return {
            "refactor_text": refactorer_context(
                state, base_dir,
                instincts=instincts, node_execution_id=execution_id,
            )
        }

    refactorer_next_phase = NODE_TO_PHASE[NODE_REFACTORER]
    return _scaffold_node(
        node_name=NODE_REFACTORER,
        llm_roles=("driver", "refactorer"),
        runner=run_refactorer,
        build_inputs=build_inputs,
        audience=InstinctAudience.REFACTORER,
        llm_configs=llm_configs,
        base_dir=base_dir,
        prompts_dir=prompts_dir,
        on_node_event=on_node_event,
        on_token=on_token,
        instinct_retriever=instinct_retriever,
        on_state_checkpoint=on_state_checkpoint,
        error_next_phase=refactorer_next_phase,
        # A malformed Refactorer response wrote no trustworthy output. Do not
        # skip straight to review of the pre-refactor tree on resume.
        error_resume_node=NODE_REFACTORER,
        check_next_phase=refactorer_next_phase,
        pause_resume_node=NEXT_NODE_ON_PAUSE[NODE_REFACTORER],
    )


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


def _make_blocked_node() -> Callable[[RunState], RunState]:
    """Terminal node for a valid role-declared missing prerequisite."""
    def node(state: RunState) -> RunState:
        return state.model_copy(update={"status": RunStatus.BLOCKED})
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
    reviewer_test_timeout_seconds: float = 300.0,
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
    architect_node = _make_architect_node(
        llm_configs, base_dir, prompts_dir,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    driver_red_node = _make_driver_node(
        "red", llm_configs, base_dir, prompts_dir,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    driver_green_node = _make_driver_node(
        "green", llm_configs, base_dir, prompts_dir,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    reviewer_red_node = _make_reviewer_node(
        CheckpointType.RED_CHECK, llm_configs, base_dir, prompts_dir,
        reviewer_test_timeout_seconds=reviewer_test_timeout_seconds,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    reviewer_green_node = _make_reviewer_node(
        CheckpointType.GREEN_CHECK, llm_configs, base_dir, prompts_dir,
        reviewer_test_timeout_seconds=reviewer_test_timeout_seconds,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    refactorer_node = _make_refactorer_node(
        llm_configs, base_dir, prompts_dir,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    reviewer_refactor_node = _make_reviewer_node(
        CheckpointType.REFACTOR_CHECK, llm_configs, base_dir, prompts_dir,
        reviewer_test_timeout_seconds=reviewer_test_timeout_seconds,
        on_state_checkpoint=on_state_checkpoint, **shared
    )
    done_node = _make_done_node()
    pause_node = _make_pause_node()
    blocked_node = _make_blocked_node()

    def checkpointed(
        node: Callable[[RunState], RunState],
    ) -> Callable[[RunState], RunState]:
        def wrapped(state: RunState) -> RunState:
            result = RunState.model_validate(node(state))
            if result.graph_progress is not None:
                result = result.model_copy(update={
                    "graph_progress": result.graph_progress.model_copy(update={
                        "stage": ProgressStage.OUTCOME_CHECKPOINTED,
                    }),
                })
            if on_state_checkpoint is not None:
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
    blocked_node = checkpointed(blocked_node)
    
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
    graph.add_node(NODE_BLOCKED, blocked_node)
    
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
            if state.status == RunStatus.BLOCKED:
                return NODE_BLOCKED
            return next_node
        return gate

    # Architect -> Driver(RED)
    graph.add_conditional_edges(
        NODE_ARCHITECT, _pause_gate(NODE_DRIVER_RED), [NODE_DRIVER_RED, NODE_PAUSE, NODE_BLOCKED]
    )

    # Driver(RED) -> Reviewer(RED_CHECK)
    graph.add_conditional_edges(
        NODE_DRIVER_RED, _pause_gate(NODE_REVIEWER_RED), [NODE_REVIEWER_RED, NODE_PAUSE, NODE_BLOCKED]
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
        lambda state: _successor(state, NODE_REVIEWER_RED),
        [NODE_DRIVER_GREEN, NODE_DRIVER_RED, NODE_PAUSE, NODE_BLOCKED],
    )

    # Driver(GREEN) -> Reviewer(GREEN_CHECK)
    graph.add_conditional_edges(
        NODE_DRIVER_GREEN, _pause_gate(NODE_REVIEWER_GREEN), [NODE_REVIEWER_GREEN, NODE_PAUSE, NODE_BLOCKED]
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
        lambda state: _successor(state, NODE_REVIEWER_GREEN),
        [NODE_REFACTORER, NODE_DRIVER_GREEN, NODE_PAUSE, NODE_BLOCKED],
    )

    # Refactorer -> Reviewer(REFACTOR_CHECK)
    graph.add_conditional_edges(
        NODE_REFACTORER, _pause_gate(NODE_REVIEWER_REFACTOR), [NODE_REVIEWER_REFACTOR, NODE_PAUSE, NODE_BLOCKED]
    )

    # Reviewer(REFACTOR_CHECK) conditional edges.
    # If accepted (tests still pass): -> DONE
    # If rejected (tests fail): -> Refactorer to retry
    graph.add_conditional_edges(
        NODE_REVIEWER_REFACTOR,
        lambda state: _successor(state, NODE_REVIEWER_REFACTOR),
        [NODE_DONE, NODE_REFACTORER, NODE_PAUSE, NODE_BLOCKED],
    )

    # PAUSE is a clean terminal within a single invoke() call: whenever an
    # interrupt fires, execution should stop here and return control to the
    # caller (the CLI), not try to keep routing internally. Resuming later
    # is handled by *where the next invoke() call starts* (see the
    # conditional entry point below), not by PAUSE routing onward — a single
    # invoke() never continues past a real pause.
    graph.add_edge(NODE_PAUSE, END)
    graph.add_edge(NODE_BLOCKED, END)
    
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
            NODE_DONE,
            NODE_PAUSE,
            NODE_BLOCKED,
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
            NODE_DONE: NODE_DONE,
            NODE_PAUSE: NODE_PAUSE,
            NODE_BLOCKED: NODE_BLOCKED,
        },
    )
    
    return graph


def _infer_resume_target(state: RunState) -> str:
    """Infer the resume target node from the last interrupt or rejection.
    
    Prefer the durable cursor; authorized pauses retain legacy target inference.
    """
    if state.graph_progress is not None and state.graph_progress.next_node not in {
        NODE_PAUSE, NODE_BLOCKED,
    }:
        return state.graph_progress.next_node
    if state.status is RunStatus.BLOCKED and state.phase in {
        NODE_DRIVER_RED, NODE_DRIVER_GREEN, NODE_REFACTORER,
    }:
        return state.phase
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
    if state.resume_target:
        return state.resume_target
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
    reviewer_test_timeout_seconds: float = 300.0,
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
    # Determine where to resume
    recovery = assess_recovery(state)
    if recovery is not None and recovery.disposition == "terminal":
        raise UnsafeRecoveryError(recovery.message)
    resume_target = _infer_resume_target(state)
    
    # Prepare state for resumption
    resume_state = state.model_copy(update={
        "resume_target": resume_target,
        "status": RunStatus.IN_PROGRESS,
        "phase": resume_target,  # Set phase to match target
    })
    
    return run_ticket(
        resume_state, llm_configs, base_dir, prompts_dir, max_turns,
        on_node_event=on_node_event, on_token=on_token,
        on_state_checkpoint=on_state_checkpoint,
        reviewer_test_timeout_seconds=reviewer_test_timeout_seconds,
    )


def run_ticket(
    initial_state: RunState,
    llm_configs: dict[str, Any],
    base_dir: str = ".",
    prompts_dir: str | None = None,
    max_turns: int = 50,
    on_node_event: Callable[[dict], None] | None = None,
    on_token: Callable[[dict], None] | None = None,
    on_state_checkpoint: Callable[[RunState], None] | None = None,
    reviewer_test_timeout_seconds: float = 300.0,
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

    latest = initial_state.model_copy(deep=True)

    def checkpoint(state: RunState) -> None:
        nonlocal latest
        if on_state_checkpoint is not None:
            on_state_checkpoint(state)
        latest = state.model_copy(deep=True)

    # Build graph
    graph = build_graph(
        llm_configs, base_dir, prompts_dir,
        on_node_event=on_node_event, on_token=on_token,
        on_state_checkpoint=checkpoint,
        reviewer_test_timeout_seconds=reviewer_test_timeout_seconds,
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
        if latest.status in {RunStatus.AWAITING_HUMAN, RunStatus.BLOCKED, RunStatus.DONE}:
            # A typed block retains its own phase and human-resolution target,
            # even when the terminal graph node cannot run within this limit.
            return latest
        return latest.model_copy(update={
            "status": RunStatus.BLOCKED,
            "phase": "recursion_limit_exceeded",
        })
