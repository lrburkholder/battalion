"""Construct handoffs from retained execution evidence, never plan prose."""

from __future__ import annotations

from battalion.artifact_targets import ArtifactTargetContract, ArtifactTargetEvidenceReference
from battalion.artifact_target_reconciliation import ArtifactTargetCurrentEvidence
from battalion.artifact_target_state import ArtifactTargetReasonCode
from battalion.state.models import RunState, RunStatus, completed_architect_handoff
from battalion.workflow_admission import AdmissionEvidenceSource
from battalion.workflow_recipes import (
    DEFAULT_WORKFLOW_RECIPE_REGISTRY, UnknownWorkflowRecipe, WorkflowRecipeRegistry, WorkflowStage,
)


class ArtifactTargetSealingRejected(ValueError):
    def __init__(self, reason: ArtifactTargetReasonCode, message: str):
        self.reason_code = reason
        super().__init__(message)


def construct_architect_target_contract(
    state: RunState,
    *,
    execution_id: str,
    current: ArtifactTargetCurrentEvidence,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> ArtifactTargetContract:
    """Seal only the latest completed Architect candidate before any Driver.

    This is initial sealing, not a human correction or an automatic replacement.
    Existing handoffs may only reconstruct the identical active contract.
    Current external evidence is reconciled separately before readiness.
    """
    reason = ArtifactTargetReasonCode
    admission = state.workflow_admission
    if admission is None or state.project_id is None:
        raise ArtifactTargetSealingRejected(
            reason.MISSING_EVIDENCE, "Legacy Run lacks admission/project evidence; create an admitted Run.",
        )
    if state.status in {RunStatus.DONE, RunStatus.FAILED_INFRA}:
        raise ArtifactTargetSealingRejected(reason.STALE_EVIDENCE, "Terminal Run evidence is immutable.")
    executions = state.execution_record.node_executions
    if any(attempt.role == "driver" for attempt in executions):
        raise ArtifactTargetSealingRejected(
            reason.STALE_EVIDENCE, "Initial sealing must precede every Driver attempt; historical attempts cannot be authorized retroactively.",
        )
    architects = [attempt for attempt in executions if attempt.role == "architect"]
    if not architects or architects[-1].execution_id != execution_id:
        raise ArtifactTargetSealingRejected(reason.STALE_EVIDENCE, "Select the latest Architect execution explicitly.")
    attempt = architects[-1]
    if len([item for item in executions if item.execution_id == execution_id]) != 1 or (
        not completed_architect_handoff(attempt, state.interrupt_log)
    ):
        raise ArtifactTargetSealingRejected(reason.MISSING_EVIDENCE, "Architect has no eligible completed handoff.")
    candidate = attempt.architect_handoff_candidate
    if candidate is None:
        raise ArtifactTargetSealingRejected(
            reason.MISSING_TARGETS, "Architect execution lacks a typed candidate; rerun Architect rather than parsing plan.md.",
        )
    plans = [artifact for artifact in attempt.artifact_provenance
             if artifact.path == "plan.md" and artifact.originating_run_id == state.run_id
             and artifact.originating_node_execution_id == execution_id]
    if len(plans) != 1:
        raise ArtifactTargetSealingRejected(reason.MISSING_EVIDENCE, "Architect lacks unique plan.md provenance.")
    recipe_id = admission.execution.continuation_recipe_id or admission.execution.recipe_id
    recipe_version = admission.execution.continuation_recipe_version or admission.execution.recipe_version
    try:
        recipe = registry.resolve(recipe_id, recipe_version)
    except UnknownWorkflowRecipe as exc:
        raise ArtifactTargetSealingRejected(reason.INCOMPATIBLE_RECIPE, str(exc)) from exc
    if WorkflowStage.ARCHITECTURE not in recipe.stages:
        raise ArtifactTargetSealingRejected(reason.INCOMPATIBLE_RECIPE, "Selected recipe does not admit Architect sealing.")
    if (current.recipe_id, current.recipe_version) != (recipe_id, recipe_version):
        raise ArtifactTargetSealingRejected(reason.INCOMPATIBLE_RECIPE, "Current evidence must name the exact admitted recipe.")
    decision = admission.decision
    if decision.specification_revision is None or current.project_source_revision is None or (
        current.project_source_revision.casefold() == "latest"
    ):
        raise ArtifactTargetSealingRejected(reason.MISSING_EVIDENCE, "Sealing requires pinned specification and project-source revisions.")
    contract = ArtifactTargetContract(
        project_id=state.project_id,
        work_item_revision=decision.work_item_revision,
        specification_revision=decision.specification_revision,
        project_source_revision=current.project_source_revision,
        workflow_admission_decision_id=decision.decision_id,
        architect_execution_id=execution_id, plan_artifact_digest=plans[0].sha256,
        targets=candidate.targets,
        evidence_references=tuple(
            ArtifactTargetEvidenceReference(
                evidence_id=ref.evidence_id, source=ref.source, source_revision=ref.source_revision,
            ) for ref in admission.assessment.evidence_references
            if ref.source in {AdmissionEvidenceSource.WORK_ITEM, AdmissionEvidenceSource.SPECIFICATION}
        ),
    )
    handoff = state.artifact_target_handoff
    if handoff is not None and handoff.corrections:
        raise ArtifactTargetSealingRejected(
            reason.STALE_EVIDENCE, "Initial sealing cannot modify human-corrected or cancelled handoff history.",
        )
    if handoff is not None and handoff.contracts and (
        handoff.active_contract_id != contract.contract_id
        or handoff.contracts[-1] != contract
    ):
        raise ArtifactTargetSealingRejected(
            reason.STALE_EVIDENCE, "Initial sealing cannot replace or reactivate a handoff; an explicit correction is required.",
        )
    return contract
