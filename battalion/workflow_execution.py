"""Application-owned execution and upgrade policy for registered workflows.

This module deliberately models execution semantics without choosing a graph or
persisting a Run.  BTN-143 owns durable Run linkage and resume storage; it must
retain these immutable records rather than recreate or reinterpret them.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from battalion.role_results import DriverReasonCode, RoleExecutionResult, RoleResultKind
from battalion.workflow_recipes import (
    CompletionRequirementKind,
    FULL_IMPLEMENTATION_RECIPE,
    WorkflowCompletionRequirement,
    WorkflowRecipe,
    WorkflowRecipeRegistry,
    WorkflowStage,
)


class WorkflowUpgradeError(ValueError):
    """Base class for rejected workflow-execution transitions."""


class WorkflowDowngradeProhibited(WorkflowUpgradeError):
    """A transition attempted to restore lower assurance after execution began."""


class WorkflowUpgradeNotRequired(WorkflowUpgradeError):
    """An upgrade request was made for a workflow that is already full."""


class WorkflowUpgradeAlreadyApplied(WorkflowDowngradeProhibited):
    """A compact workflow has already stopped for stronger handling."""


class WorkflowUpgradeTrigger(str, Enum):
    """Facts that must ratchet compact execution to stronger handling."""

    SPECIFICATION_AMBIGUITY = "specification-ambiguity"
    ARCHITECTURE_DECISION = "architecture-decision"
    UNANTICIPATED_DOMAIN_SCOPE = "unanticipated-domain-scope"
    INTERFACE_SCHEMA_PERSISTENCE_MIGRATION_INTEGRATION = (
        "interface-schema-persistence-migration-integration"
    )
    AUTH_SECRETS_PRIVACY_SECURITY = "auth-secrets-privacy-security"
    REQUIRED_VERIFICATION_UNAVAILABLE = "required-verification-unavailable"
    MATERIAL_GATE_FAILURE = "material-gate-failure"
    MATERIAL_INDEPENDENT_REVIEW_CONCERN = "material-independent-review-concern"
    WRITE_SCOPE_EXCEEDANCE = "write-scope-exceedance"
    CONFIGURED_FULL_ONLY_CONDITION = "configured-full-only-condition"


class WorkflowUpgradeTarget(str, Enum):
    """The stronger non-executing or full-execution dispositions."""

    CLARIFICATION = "clarification"
    FULL = "full"


_CLARIFICATION_TRIGGERS = frozenset(
    {
        WorkflowUpgradeTrigger.SPECIFICATION_AMBIGUITY,
        WorkflowUpgradeTrigger.REQUIRED_VERIFICATION_UNAVAILABLE,
    }
)


class WorkflowStageEvidence(BaseModel):
    """Durable-ready evidence that a compact stage completed before an upgrade."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    stage: WorkflowStage
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "WorkflowStageEvidence":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("stage evidence IDs must be unique")
        return self


class WorkflowCompletionEvidence(BaseModel):
    """Evidence satisfying one ordered recipe completion requirement."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: CompletionRequirementKind
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "WorkflowCompletionEvidence":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("completion evidence IDs must be unique")
        return self


class WorkflowUpgradeRecord(BaseModel):
    """One irreversible assurance increase with its triggering evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    trigger: WorkflowUpgradeTrigger
    target: WorkflowUpgradeTarget
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "WorkflowUpgradeRecord":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("upgrade evidence IDs must be unique")
        return self


class WorkflowExecutionState(BaseModel):
    """Immutable execution semantics derived from an admitted exact recipe.

    The records are intentionally persistence-ready: a later durable Run owner
    can retain selected recipe identity, completed evidence, and each ratchet
    event without losing the compact history that preceded an upgrade.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    recipe_id: str = Field(min_length=1, max_length=200)
    recipe_version: str = Field(min_length=1, max_length=100)
    completed_stage_evidence: tuple[WorkflowStageEvidence, ...] = Field(
        default_factory=tuple, max_length=20
    )
    completion_evidence: tuple[WorkflowCompletionEvidence, ...] = Field(
        default_factory=tuple, max_length=20
    )
    upgrade_history: tuple[WorkflowUpgradeRecord, ...] = Field(
        default_factory=tuple, max_length=20
    )
    upgrade_target: WorkflowUpgradeTarget | None = None
    continuation_recipe_id: str | None = Field(default=None, min_length=1, max_length=200)
    continuation_recipe_version: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_stage_evidence_is_unique(self) -> "WorkflowExecutionState":
        stages = [record.stage for record in self.completed_stage_evidence]
        if len(stages) != len(set(stages)):
            raise ValueError("completed stage evidence may record each stage only once")
        completion_kinds = [record.kind for record in self.completion_evidence]
        if len(completion_kinds) != len(set(completion_kinds)):
            raise ValueError("completion evidence may record each requirement only once")
        has_continuation_id = self.continuation_recipe_id is not None
        has_continuation_version = self.continuation_recipe_version is not None
        if has_continuation_id != has_continuation_version:
            raise ValueError("continuation recipes require an exact recipe version")
        if not self.upgrade_history:
            if self.upgrade_target is not None or has_continuation_id:
                raise ValueError("only an upgraded workflow may name a continuation")
        elif self.upgrade_target is None:
            raise ValueError("upgraded workflows require an upgrade target")
        elif self.upgrade_target is WorkflowUpgradeTarget.FULL and not has_continuation_id:
            raise ValueError("full upgrades require a full-workflow continuation recipe")
        elif self.upgrade_target is WorkflowUpgradeTarget.CLARIFICATION and has_continuation_id:
            raise ValueError("clarification upgrades cannot name an execution continuation")
        return self


def start_workflow_execution(recipe: WorkflowRecipe) -> WorkflowExecutionState:
    """Create execution state from the exact recipe selected by admission."""
    return WorkflowExecutionState(
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.recipe_version,
    )


def record_workflow_stage(
    execution: WorkflowExecutionState,
    evidence: WorkflowStageEvidence,
    *,
    registry: WorkflowRecipeRegistry,
) -> WorkflowExecutionState:
    """Retain stage evidence only when it belongs to the selected recipe."""
    recipe = _active_recipe(execution, registry)
    if evidence.stage not in recipe.stages:
        raise WorkflowUpgradeError(
            f"stage {evidence.stage.value!r} does not belong to the selected workflow recipe"
        )
    if any(record.stage is evidence.stage for record in execution.completed_stage_evidence):
        raise WorkflowUpgradeError(f"stage {evidence.stage.value!r} already has recorded evidence")
    return execution.model_copy(
        update={"completed_stage_evidence": (*execution.completed_stage_evidence, evidence)}
    )


def record_workflow_completion(
    execution: WorkflowExecutionState,
    evidence: WorkflowCompletionEvidence,
    *,
    registry: WorkflowRecipeRegistry,
) -> WorkflowExecutionState:
    """Retain ordered completion evidence without collapsing a related Run.

    ``SEMANTIC_REVIEW`` evidence refers to the independent Review Run/result;
    it does not cause the legacy checkpoint reviewer to gain semantic-review
    authority inside the Implementation Run.
    """
    recipe = _active_recipe(execution, registry)
    _require_all_stages_completed(execution, recipe)
    requirement = _completion_requirement(recipe, evidence.kind)
    if any(record.kind is evidence.kind for record in execution.completion_evidence):
        raise WorkflowUpgradeError(f"completion requirement {evidence.kind.value!r} is already met")
    requirement_index = recipe.completion_requirements.index(requirement)
    required_prior = recipe.completion_requirements[:requirement_index]
    recorded = {record.kind for record in execution.completion_evidence}
    missing_prior = [requirement.kind.value for requirement in required_prior if requirement.kind not in recorded]
    if missing_prior:
        raise WorkflowUpgradeError(
            "completion requirements must be evidenced in recipe order; missing "
            + ", ".join(missing_prior)
        )
    return execution.model_copy(
        update={"completion_evidence": (*execution.completion_evidence, evidence)}
    )


def workflow_is_complete(
    execution: WorkflowExecutionState,
    *,
    registry: WorkflowRecipeRegistry,
) -> bool:
    """Return whether the active admitted recipe has all required evidence."""
    if execution.upgrade_target is not None:
        return False
    recipe = registry.resolve(execution.recipe_id, execution.recipe_version)
    stages = {record.stage for record in execution.completed_stage_evidence}
    completions = {record.kind for record in execution.completion_evidence}
    return set(recipe.stages) <= stages and {
        requirement.kind for requirement in recipe.completion_requirements
    } <= completions


def upgrade_workflow_execution(
    execution: WorkflowExecutionState,
    *,
    trigger: WorkflowUpgradeTrigger,
    reason: str,
    evidence_ids: tuple[str, ...],
    registry: WorkflowRecipeRegistry,
) -> WorkflowExecutionState:
    """Ratchet compact execution upward without discarding completed evidence.

    The target comes only from Battalion policy.  No model recommendation,
    presentation client, or caller-selected target can weaken assurance.
    """
    recipe = registry.resolve(execution.recipe_id, execution.recipe_version)
    if recipe.recipe_id == FULL_IMPLEMENTATION_RECIPE.recipe_id:
        raise WorkflowUpgradeNotRequired("the full workflow cannot be upgraded further")
    if recipe.recipe_id != "compact-implementation-run":
        raise WorkflowUpgradeError(
            f"workflow recipe {recipe.recipe_id!r} has no registered upgrade policy"
        )

    target = (
        WorkflowUpgradeTarget.CLARIFICATION
        if trigger in _CLARIFICATION_TRIGGERS
        else WorkflowUpgradeTarget.FULL
    )
    record = WorkflowUpgradeRecord(
        trigger=trigger,
        target=target,
        reason=reason,
        evidence_ids=evidence_ids,
    )
    if execution.upgrade_history:
        raise WorkflowUpgradeAlreadyApplied(
            "workflow execution has already upgraded and cannot return to compact assurance"
        )
    continuation = (
        (FULL_IMPLEMENTATION_RECIPE.recipe_id, FULL_IMPLEMENTATION_RECIPE.recipe_version)
        if target is WorkflowUpgradeTarget.FULL
        else (None, None)
    )
    return execution.model_copy(
        update={
            "upgrade_history": (record,),
            "upgrade_target": target,
            "continuation_recipe_id": continuation[0],
            "continuation_recipe_version": continuation[1],
        }
    )


def upgrade_for_driver_result(
    execution: WorkflowExecutionState,
    result: RoleExecutionResult,
    *,
    evidence_ids: tuple[str, ...],
    registry: WorkflowRecipeRegistry,
) -> WorkflowExecutionState:
    """Apply compact ratchet policy from a validated BTN-133 Driver outcome.

    The Driver supplies a typed outcome but never chooses an upgrade target.
    Missing context remains a typed blocked condition for application resume
    policy; it is not silently rewritten as a stronger recipe selection.
    """
    trigger = upgrade_trigger_for_driver_result(result)
    if trigger is None:
        return execution
    return upgrade_workflow_execution(
        execution,
        trigger=trigger,
        reason=result.summary or result.reason_code.value,
        evidence_ids=evidence_ids,
        registry=registry,
    )


def upgrade_trigger_for_driver_result(
    result: RoleExecutionResult,
) -> WorkflowUpgradeTrigger | None:
    """Map a validated Driver outcome to policy facts, never workflow choice."""
    if result.kind is RoleResultKind.ESCALATED:
        mapping = {
            DriverReasonCode.SPECIFICATION_AMBIGUITY: WorkflowUpgradeTrigger.SPECIFICATION_AMBIGUITY,
            DriverReasonCode.ARCHITECTURAL_DECISION_REQUIRED: WorkflowUpgradeTrigger.ARCHITECTURE_DECISION,
            DriverReasonCode.AUTHORITATIVE_EVIDENCE_CONFLICT: WorkflowUpgradeTrigger.SPECIFICATION_AMBIGUITY,
        }
        return mapping.get(result.reason_code)
    if (
        result.kind is RoleResultKind.BLOCKED
        and result.reason_code is DriverReasonCode.INSUFFICIENT_WRITE_SCOPE
    ):
        return WorkflowUpgradeTrigger.WRITE_SCOPE_EXCEEDANCE
    return None


def _active_recipe(
    execution: WorkflowExecutionState, registry: WorkflowRecipeRegistry
) -> WorkflowRecipe:
    if execution.upgrade_target is not None:
        raise WorkflowUpgradeAlreadyApplied(
            "workflow execution has upgraded and cannot continue under compact assurance"
        )
    return registry.resolve(execution.recipe_id, execution.recipe_version)


def _require_all_stages_completed(
    execution: WorkflowExecutionState, recipe: WorkflowRecipe
) -> None:
    completed = {record.stage for record in execution.completed_stage_evidence}
    missing = [stage.value for stage in recipe.stages if stage not in completed]
    if missing:
        raise WorkflowUpgradeError(
            "completion evidence requires all workflow stages first; missing " + ", ".join(missing)
        )


def _completion_requirement(
    recipe: WorkflowRecipe, kind: CompletionRequirementKind
) -> WorkflowCompletionRequirement:
    for requirement in recipe.completion_requirements:
        if requirement.kind is kind:
            return requirement
    raise WorkflowUpgradeError(
        f"completion requirement {kind.value!r} does not belong to the selected workflow recipe"
    )
