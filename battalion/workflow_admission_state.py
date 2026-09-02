"""Durable linkage between workflow-admission evidence and one Run.

Admission and Tactician assessments remain distinct pre-admission records.  This
module groups them with the authorized decision and exact execution policy only
at the persistence boundary; it does not turn either assessment into a graph or
model-node execution.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from battalion.tactician import TacticianAssessment
from battalion.workflow_admission import (
    AdmissionEvidenceSource,
    WorkflowAdmissionEvidence,
    WorkflowAdmissionPolicy,
    WorkflowAdmissionAssessment,
    WorkflowAdmissionOutcome,
    assess_workflow_admission,
)
from battalion.workflow_admission_decisions import (
    WorkflowAdmissionDecision,
    WorkflowAdmissionDisposition,
)
from battalion.workflow_execution import WorkflowExecutionState
from battalion.workflow_recipes import UnknownWorkflowRecipe, WorkflowRecipeRegistry


class WorkflowAdmissionResumeError(ValueError):
    """Base class for persisted admission state that cannot safely resume."""


class UnknownPersistedWorkflowRecipe(WorkflowAdmissionResumeError):
    """An exact historical recipe key is unavailable to the current runtime."""


class StalePersistedWorkflowAdmission(WorkflowAdmissionResumeError):
    """Current policy or evidence no longer matches the admitted snapshot."""


class InconsistentPersistedWorkflowAdmission(WorkflowAdmissionResumeError):
    """Persisted disposition and registered recipe semantics disagree."""


class WorkflowAdmissionRunRecord(BaseModel):
    """Versioned, immutable admission and execution linkage for one Run.

    The complete assessment records are retained independently rather than
    flattened into the human decision or execution state.  Cross-record
    validation makes corrupt or partially rewritten persistence fail closed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    assessment: WorkflowAdmissionAssessment
    tactician_assessment: TacticianAssessment | None = None
    decision: WorkflowAdmissionDecision
    execution: WorkflowExecutionState

    @model_validator(mode="after")
    def validate_linkage(self) -> Self:
        assessment = self.assessment
        decision = self.decision
        tactician = self.tactician_assessment

        if decision.disposition not in {
            WorkflowAdmissionDisposition.FULL,
            WorkflowAdmissionDisposition.COMPACT,
        }:
            raise ValueError(
                "only an execution admission can be linked to a durable Run"
            )
        if decision.admission_assessment_id != assessment.assessment_id:
            raise ValueError("admission decision references a different assessment")
        if (
            decision.work_item_revision != assessment.work_item_revision
            or decision.specification_revision != assessment.specification_revision
        ):
            raise ValueError("admission decision revisions do not match its assessment")
        if (
            decision.policy_id != assessment.policy_id
            or decision.policy_version != assessment.policy_version
        ):
            raise ValueError("admission decision policy does not match its assessment")

        if tactician is None:
            if decision.tactician_assessment_id is not None:
                raise ValueError("admission decision references missing Tactician evidence")
            if (
                assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
                and decision.disposition is WorkflowAdmissionDisposition.COMPACT
            ):
                raise ValueError(
                    "uncertain compact admission requires retained Tactician evidence"
                )
        else:
            if assessment.outcome is not WorkflowAdmissionOutcome.UNCERTAIN:
                raise ValueError(
                    "Tactician evidence can only accompany an uncertain assessment"
                )
            if decision.tactician_assessment_id != tactician.assessment_id:
                raise ValueError("admission decision references different Tactician evidence")
            assessed_evidence = {
                (
                    reference.evidence_id,
                    reference.source,
                    reference.source_revision,
                )
                for reference in assessment.evidence_references
            }
            if any(
                (
                    reference.evidence_id,
                    reference.source,
                    reference.source_revision,
                )
                not in assessed_evidence
                for reference in tactician.input_evidence_references
            ):
                raise ValueError(
                    "Tactician evidence does not belong to the persisted assessment"
                )
            if not any(
                reference.source is AdmissionEvidenceSource.WORK_ITEM
                and reference.source_revision == assessment.work_item_revision
                for reference in tactician.input_evidence_references
            ):
                raise ValueError(
                    "Tactician evidence omits the assessed work-item revision"
                )
            if assessment.specification_revision is not None and not any(
                reference.source is AdmissionEvidenceSource.SPECIFICATION
                and reference.source_revision == assessment.specification_revision
                for reference in tactician.input_evidence_references
            ):
                raise ValueError(
                    "Tactician evidence omits the assessed specification revision"
                )

        expected_risk_flags = set(assessment.hard_risk_flags)
        if tactician is not None:
            expected_risk_flags.update(tactician.risk_flags)
        if set(decision.admitted_risk_flags) != expected_risk_flags:
            raise ValueError("admitted risk flags do not match the retained evidence")

        selected_recipe = (
            decision.selected_recipe_id,
            decision.selected_recipe_version,
        )
        execution_recipe = (self.execution.recipe_id, self.execution.recipe_version)
        if selected_recipe != execution_recipe:
            raise ValueError("workflow execution does not use the admitted exact recipe")
        if (
            assessment.outcome is WorkflowAdmissionOutcome.FULL_REQUIRED
            and decision.disposition is not WorkflowAdmissionDisposition.FULL
        ):
            raise ValueError("full-required evidence cannot persist a compact admission")
        return self


def validate_workflow_admission_resume(
    record: WorkflowAdmissionRunRecord,
    *,
    registry: WorkflowRecipeRegistry,
    policy: WorkflowAdmissionPolicy,
    current_evidence: WorkflowAdmissionEvidence | None = None,
) -> WorkflowExecutionState:
    """Validate exact persisted semantics without re-running Tactician.

    A process restart may resume directly from the retained record.  When the
    caller has refreshed authoritative evidence, it may supply that snapshot;
    any difference requires explicit re-admission or stronger handling.
    """

    execution = validate_persisted_workflow_recipes(record, registry=registry)
    assessment = record.assessment
    if (
        assessment.policy_id != policy.policy_id
        or assessment.policy_version != policy.policy_version
    ):
        raise StalePersistedWorkflowAdmission(
            "persisted workflow admission uses a different policy version; "
            "re-admission or stronger handling is required"
        )
    selected_recipe_id = record.decision.selected_recipe_id
    if (
        record.decision.disposition is WorkflowAdmissionDisposition.FULL
        and selected_recipe_id != policy.full_recipe_id
    ):
        raise InconsistentPersistedWorkflowAdmission(
            "persisted full admission does not select the policy's full recipe"
        )
    if (
        record.decision.disposition is WorkflowAdmissionDisposition.COMPACT
        and selected_recipe_id not in policy.compact_recipe_ids
    ):
        raise InconsistentPersistedWorkflowAdmission(
            "persisted compact admission does not select a policy compact recipe"
        )
    if current_evidence is not None:
        current = assess_workflow_admission(current_evidence, policy=policy)
        if current != assessment:
            raise StalePersistedWorkflowAdmission(
                "current work-item, specification, or evidence no longer matches "
                "the persisted admission; re-admission or stronger handling is required"
            )
    return execution


def validate_persisted_workflow_recipes(
    record: WorkflowAdmissionRunRecord,
    *,
    registry: WorkflowRecipeRegistry,
) -> WorkflowExecutionState:
    """Resolve every exact recipe key needed to interpret retained history."""

    execution = record.execution
    try:
        registry.resolve(execution.recipe_id, execution.recipe_version)
        if execution.continuation_recipe_id is not None:
            registry.resolve(
                execution.continuation_recipe_id,
                execution.continuation_recipe_version or "",
            )
    except UnknownWorkflowRecipe as exc:
        raise UnknownPersistedWorkflowRecipe(str(exc)) from exc
    return execution
