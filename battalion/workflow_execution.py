"""Application-owned execution and upgrade policy for registered workflows.

This module deliberately models execution semantics without choosing a graph or
persisting a Run.  BTN-143 owns durable Run linkage and resume storage; it must
retain these immutable records rather than recreate or reinterpret them.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from battalion.workflow_recipes import (
    FULL_IMPLEMENTATION_RECIPE,
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
    upgrade_history: tuple[WorkflowUpgradeRecord, ...] = Field(
        default_factory=tuple, max_length=20
    )

    @model_validator(mode="after")
    def validate_stage_evidence_is_unique(self) -> "WorkflowExecutionState":
        stages = [record.stage for record in self.completed_stage_evidence]
        if len(stages) != len(set(stages)):
            raise ValueError("completed stage evidence may record each stage only once")
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
    recipe = registry.resolve(execution.recipe_id, execution.recipe_version)
    if evidence.stage not in recipe.stages:
        raise WorkflowUpgradeError(
            f"stage {evidence.stage.value!r} does not belong to the selected workflow recipe"
        )
    if any(record.stage is evidence.stage for record in execution.completed_stage_evidence):
        raise WorkflowUpgradeError(f"stage {evidence.stage.value!r} already has recorded evidence")
    return execution.model_copy(
        update={"completed_stage_evidence": (*execution.completed_stage_evidence, evidence)}
    )


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
        raise WorkflowDowngradeProhibited(
            "workflow execution has already upgraded and cannot return to compact assurance"
        )
    return execution.model_copy(update={"upgrade_history": (record,)})
