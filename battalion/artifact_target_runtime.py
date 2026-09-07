"""Application-owned source observation and pre-Driver admission policy."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

from battalion.artifact_target_reconciliation import ArtifactTargetCurrentEvidence, reconcile_artifact_targets
from battalion.artifact_target_sealing import ArtifactTargetSealingRejected, construct_architect_target_contract
from battalion.artifact_target_sources import current_project_source_revision, verified_run_write_digests
from battalion.artifact_target_state import ArtifactTargetCorrection, ArtifactTargetHandoffRecord, ArtifactTargetReasonCode as Reason
from battalion.artifact_targets import ArtifactTargetContract, normalize_target_path
from battalion.project_source import ProjectSourceChanged
from battalion.project_source_files import capture_project_source
from battalion.scope.artifact_target_paths import inspect_artifact_target_paths
from battalion.scope.tool_binding import normalize_scope_root
from battalion.state.models import ProgressStage, RunState, RunStatus
from battalion.workflow_recipes import DEFAULT_WORKFLOW_RECIPE_REGISTRY


class ArtifactTargetGateRejected(ArtifactTargetSealingRejected):
    """No Driver attempt may begin; state retains any new reconciliation."""

    def __init__(self, reason, message, state):
        self.state = state
        super().__init__(reason, message)


def reconcile_run_handoff(
    state: RunState, *, project_root, current: ArtifactTargetCurrentEvidence,
    case_sensitive_paths: bool, initial_only: bool = False,
    registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY, clock=None,
    replacement_contract: ArtifactTargetContract | None = None,
    correction: ArtifactTargetCorrection | None = None,
) -> RunState:
    """Observe current files and append evidence without granting human authority.

    The caller verifies the project marker and owns persistence/worker exclusion.
    Initial sealing and live admission use exactly the same observation policy.
    """
    root = Path(project_root).resolve(strict=True)
    baseline = state.project_source_snapshot
    if baseline is None:
        raise ArtifactTargetSealingRejected(Reason.MISSING_EVIDENCE, "Run has no pre-execution source snapshot; create a new admitted Run in a Git worktree.")
    if current.project_source_revision not in {None, baseline.revision}:
        raise ArtifactTargetSealingRejected(Reason.STALE_EVIDENCE, "Caller-supplied source evidence cannot replace the Run's captured baseline.")
    observed = capture_project_source(
        root, project_id=state.project_id,
        include_paths=tuple({item.path for item in baseline.files} | verified_run_write_digests(state).keys()),
    )
    unresolved = set(current.unresolved_reasons)
    try:
        source_revision = current_project_source_revision(state, observed)
    except ProjectSourceChanged:
        source_revision = observed.revision
        unresolved.add(Reason.STALE_EVIDENCE)
    history = state.artifact_target_handoff or ArtifactTargetHandoffRecord()
    if replacement_contract is not None:
        if correction is None or correction.corrected_contract_id != replacement_contract.contract_id:
            raise ArtifactTargetSealingRejected(Reason.MISSING_EVIDENCE, "Replacement requires its exact correction action.")
        contract = replacement_contract
    elif initial_only or not history.contracts:
        architects = [item for item in state.execution_record.node_executions if item.role == "architect"]
        contract = construct_architect_target_contract(
            state, execution_id=current.architect_execution_id or (architects[-1].execution_id if architects else "missing"),
            current=ArtifactTargetCurrentEvidence.model_validate({
                **current.model_dump(), "project_source_revision": baseline.revision,
            }), registry=registry,
        )
    else:
        contract = next((item for item in history.contracts if item.contract_id == history.active_contract_id), None)
        if contract is None:
            raise ArtifactTargetSealingRejected(Reason.MISSING_TARGETS, "Run has no active target contract; a human correction is required.")
        architects = [item for item in state.execution_record.node_executions if item.role == "architect"]
        if contract.architect_execution_id is not None and (
            not architects or architects[-1].execution_id != contract.architect_execution_id
            or current.architect_execution_id not in {None, contract.architect_execution_id}
        ):
            unresolved.add(Reason.STALE_EVIDENCE)
    actual_plan_digest = None
    if contract.architect_execution_id is not None:
        plan_path = normalize_scope_root("plan.md", root)
        normalize_target_path(plan_path.relative_to(root).as_posix())
        with plan_path.open("rb") as stream:
            actual_plan_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    current = ArtifactTargetCurrentEvidence.model_validate({
        **current.model_dump(), "project_source_revision": source_revision,
        "architect_execution_id": contract.architect_execution_id,
        "plan_artifact_digest": actual_plan_digest,
        "unresolved_reasons": tuple(sorted(unresolved, key=lambda item: item.value)),
    })
    paths = inspect_artifact_target_paths(
        contract, write_scope=state.write_scope, base_dir=root,
        case_sensitive_paths=case_sensitive_paths,
    )
    previous = history.reconciliations[-1] if history.reconciliations and replacement_contract is None else None
    result = reconcile_artifact_targets(
        contract, current=current, paths=paths, previous=previous, registry=registry,
        reconciliation_id=f"reconciliation-{uuid4()}",
        occurred_at=(clock or (lambda: datetime.now(timezone.utc)))(),
    )
    if previous is not None and previous.model_dump(exclude={"reconciliation_id", "occurred_at"}) == result.model_dump(exclude={"reconciliation_id", "occurred_at"}):
        return state
    if previous is not None and previous.outcome == "clarification-required":
        raise ArtifactTargetSealingRejected(Reason.STALE_EVIDENCE, "A recorded clarification requires a human correction; initial sealing cannot approve it.")
    handoff = ArtifactTargetHandoffRecord(
        contracts=(*history.contracts, contract) if replacement_contract is not None else history.contracts or (contract,),
        reconciliations=(*history.reconciliations, result),
        corrections=(*history.corrections, correction) if correction is not None else history.corrections,
        active_contract_id=contract.contract_id,
    )
    return RunState.model_validate({**state.model_dump(), "schema_version": "1.2", "artifact_target_handoff": handoff})


def admit_driver_attempt(state: RunState, *, project_root, current, node_name: str) -> RunState:
    """Fail closed before capture, budget, context construction, or scoped tools."""
    from battalion.identity import load_project_identity

    try:
        if state.artifact_target_handoff and state.artifact_target_handoff.corrections and state.artifact_target_handoff.corrections[-1].action == "cancel":
            raise ArtifactTargetSealingRejected(Reason.STALE_EVIDENCE, "Cancelled handoff cannot authorize Driver; create a new Run.")
        if state.status in {RunStatus.DONE, RunStatus.FAILED_INFRA}:
            raise ArtifactTargetSealingRejected(Reason.STALE_EVIDENCE, "Terminal Run cannot authorize another Driver attempt.")
        if current is None:
            raise ArtifactTargetSealingRejected(Reason.MISSING_EVIDENCE, "Driver requires current artifact-target evidence; inspect the handoff and supply revision-pinned evidence.")
        if str(load_project_identity(project_root).project_id) != state.project_id:
            raise ArtifactTargetSealingRejected(Reason.STALE_EVIDENCE, "Project marker does not match this Run.")
        if state.graph_progress and state.graph_progress.stage is ProgressStage.ATTEMPT_CREATED:
            attempt = next((item for item in state.execution_record.node_executions
                            if item.execution_id == state.graph_progress.execution_id), None)
            if (attempt is None or attempt.phase != node_name or state.artifact_target_handoff is None
                    or attempt.artifact_target_contract_id != state.artifact_target_handoff.active_contract_id):
                raise ArtifactTargetSealingRejected(Reason.STALE_EVIDENCE, "Unstarted Driver attempt must retain its original contract identity.")
        updated = reconcile_run_handoff(
            state, project_root=project_root, current=current,
            # Conservative across supported filesystems; callers cannot relax it.
            case_sensitive_paths=False,
        )
        result = updated.artifact_target_handoff.reconciliations[-1]
        if result.outcome != "ready":
            raise ArtifactTargetGateRejected(result.reason_codes[0], "Driver target handoff requires human clarification.", updated)
        checkpoints = [(index, item) for index, item in enumerate(updated.interrupt_log)
                       if item.trigger == "manual-checkpoint"
                       and item.context.get("next_phase") in {"driver_red", "driver_green"}]
        if checkpoints:
            index, checkpoint = checkpoints[-1]
            actions = [action for action in updated.human_action_log
                       if action.target == f"interrupt:{index}" and action.kind == "interrupt-resolution"]
            if (checkpoint.resolution is None or len(actions) != 1
                    or actions[0].artifact_target_contract_id != updated.artifact_target_handoff.active_contract_id
                    or actions[0].actor_id is None or actions[0].disposition != "applied"
                    or actions[0].detail != checkpoint.resolution):
                raise ArtifactTargetGateRejected(Reason.MISSING_EVIDENCE, "Driver checkpoint requires exact contract-bound human authorization.", updated)
        contract = next(item for item in updated.artifact_target_handoff.contracts
                        if item.contract_id == updated.artifact_target_handoff.active_contract_id)
        phase = node_name.replace("_", "-")
        if not any(assignment.owner_role == "driver" and assignment.workflow_phase.value == phase
                   for target in contract.targets for assignment in target.assignments):
            raise ArtifactTargetSealingRejected(Reason.INCOMPATIBLE_RECIPE, "Active contract does not admit this Driver phase.")
        return updated
    except ArtifactTargetGateRejected:
        raise
    except ArtifactTargetSealingRejected as exc:
        raise ArtifactTargetGateRejected(exc.reason_code, str(exc), state) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactTargetGateRejected(Reason.MISSING_EVIDENCE, f"Cannot verify Driver handoff: {exc}", state) from exc
