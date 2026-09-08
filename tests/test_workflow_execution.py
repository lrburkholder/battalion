"""Contract tests for BTN-142 compact execution and upgrade-only policy."""

import pytest

from battalion.application import (
    InspectWorkflowCompletion,
    RecordWorkflowCompletion,
    RecordWorkflowStage,
    StartWorkflowExecution,
    UpgradeWorkflowForDriverResult,
    UpgradeWorkflowExecution,
    record_workflow_stage,
    start_workflow_execution,
    upgrade_workflow_execution,
    upgrade_workflow_for_driver_result,
    record_workflow_completion,
    workflow_is_complete,
)
from battalion.workflow_execution import (
    WorkflowCompletionEvidence,
    WorkflowDowngradeProhibited,
    WorkflowStageEvidence,
    WorkflowUpgradeNotRequired,
    WorkflowUpgradeTarget,
    WorkflowUpgradeTrigger,
)
from battalion.workflow_recipes import CompletionRequirementKind, WorkflowStage
from battalion.role_results import DriverReasonCode, RoleExecutionResult, RoleResultKind


def _compact_execution():
    return start_workflow_execution(
        StartWorkflowExecution(
            recipe_id="compact-implementation-run",
            recipe_version="1.0",
        )
    )


def test_compact_execution_is_initialized_from_the_registered_recipe() -> None:
    execution = _compact_execution()

    assert execution.recipe_id == "compact-implementation-run"
    assert execution.recipe_version == "1.0"
    assert execution.completed_stage_evidence == ()
    assert execution.upgrade_history == ()


def test_compact_preserves_red_green_and_independent_review_evidence_before_upgrade() -> None:
    execution = _compact_execution()
    for stage in (
        WorkflowStage.DRIVER_RED,
        WorkflowStage.DRIVER_GREEN,
        WorkflowStage.REVIEW_GREEN,
    ):
        execution = record_workflow_stage(
            RecordWorkflowStage(
                execution=execution,
                evidence=WorkflowStageEvidence(
                    stage=stage,
                    evidence_ids=(f"evidence-{stage.value}",),
                ),
            )
        )

    upgraded = upgrade_workflow_execution(
        UpgradeWorkflowExecution(
            execution=execution,
            trigger=WorkflowUpgradeTrigger.MATERIAL_INDEPENDENT_REVIEW_CONCERN,
            reason="Independent review found a material cross-boundary concern.",
            evidence_ids=("review-finding-1",),
        )
    )

    assert upgraded.completed_stage_evidence == execution.completed_stage_evidence
    assert upgraded.upgrade_history[0].target is WorkflowUpgradeTarget.FULL
    assert upgraded.continuation_recipe_id == "full-implementation-run"


def test_compact_requires_related_semantic_review_then_human_acceptance_before_completion() -> None:
    execution = _compact_execution()
    for stage in (
        WorkflowStage.DRIVER_RED,
        WorkflowStage.DRIVER_GREEN,
        WorkflowStage.REVIEW_GREEN,
    ):
        execution = record_workflow_stage(
            RecordWorkflowStage(
                execution=execution,
                evidence=WorkflowStageEvidence(
                    stage=stage,
                    evidence_ids=(f"evidence-{stage.value}",),
                ),
            )
        )

    assert workflow_is_complete(InspectWorkflowCompletion(execution)) is False
    execution = record_workflow_completion(
        RecordWorkflowCompletion(
            execution=execution,
            evidence=WorkflowCompletionEvidence(
                kind=CompletionRequirementKind.SEMANTIC_REVIEW,
                evidence_ids=("review-run:review-42",),
            ),
        )
    )
    assert workflow_is_complete(InspectWorkflowCompletion(execution)) is False
    execution = record_workflow_completion(
        RecordWorkflowCompletion(
            execution=execution,
            evidence=WorkflowCompletionEvidence(
                kind=CompletionRequirementKind.HUMAN_ACCEPTANCE,
                evidence_ids=("human-action:accept-42",),
            ),
        )
    )

    assert workflow_is_complete(InspectWorkflowCompletion(execution)) is True


@pytest.mark.parametrize(
    ("trigger", "target"),
    [
        (WorkflowUpgradeTrigger.SPECIFICATION_AMBIGUITY, WorkflowUpgradeTarget.CLARIFICATION),
        (WorkflowUpgradeTrigger.ARCHITECTURE_DECISION, WorkflowUpgradeTarget.FULL),
        (WorkflowUpgradeTrigger.UNANTICIPATED_DOMAIN_SCOPE, WorkflowUpgradeTarget.FULL),
        (
            WorkflowUpgradeTrigger.INTERFACE_SCHEMA_PERSISTENCE_MIGRATION_INTEGRATION,
            WorkflowUpgradeTarget.FULL,
        ),
        (WorkflowUpgradeTrigger.AUTH_SECRETS_PRIVACY_SECURITY, WorkflowUpgradeTarget.FULL),
        (
            WorkflowUpgradeTrigger.REQUIRED_VERIFICATION_UNAVAILABLE,
            WorkflowUpgradeTarget.CLARIFICATION,
        ),
        (WorkflowUpgradeTrigger.MATERIAL_GATE_FAILURE, WorkflowUpgradeTarget.FULL),
        (WorkflowUpgradeTrigger.MATERIAL_INDEPENDENT_REVIEW_CONCERN, WorkflowUpgradeTarget.FULL),
        (WorkflowUpgradeTrigger.WRITE_SCOPE_EXCEEDANCE, WorkflowUpgradeTarget.FULL),
        (WorkflowUpgradeTrigger.CONFIGURED_FULL_ONLY_CONDITION, WorkflowUpgradeTarget.FULL),
    ],
)
def test_each_compact_upgrade_trigger_has_a_deterministic_stronger_target(trigger, target) -> None:
    upgraded = upgrade_workflow_execution(
        UpgradeWorkflowExecution(
            execution=_compact_execution(),
            trigger=trigger,
            reason="The application received authoritative trigger evidence.",
            evidence_ids=(f"evidence-{trigger.value}",),
        )
    )

    record = upgraded.upgrade_history[0]
    assert record.trigger is trigger
    assert record.target is target


def test_full_execution_cannot_be_downgraded_or_upgraded_again() -> None:
    full = start_workflow_execution(
        StartWorkflowExecution(recipe_id="full-implementation-run", recipe_version="1.0")
    )

    with pytest.raises(WorkflowUpgradeNotRequired, match="cannot be upgraded"):
        upgrade_workflow_execution(
            UpgradeWorkflowExecution(
                execution=full,
                trigger=WorkflowUpgradeTrigger.MATERIAL_GATE_FAILURE,
                reason="A material gate failed.",
                evidence_ids=("gate-1",),
            )
        )


def test_compact_execution_cannot_downgrade_after_an_upgrade() -> None:
    upgraded = upgrade_workflow_execution(
        UpgradeWorkflowExecution(
            execution=_compact_execution(),
            trigger=WorkflowUpgradeTrigger.ARCHITECTURE_DECISION,
            reason="A new architectural decision is required.",
            evidence_ids=("architecture-question-1",),
        )
    )

    with pytest.raises(WorkflowDowngradeProhibited, match="cannot return to compact"):
        upgrade_workflow_execution(
            UpgradeWorkflowExecution(
                execution=upgraded,
                trigger=WorkflowUpgradeTrigger.MATERIAL_GATE_FAILURE,
                reason="A gate failed after the architecture escalation.",
                evidence_ids=("gate-2",),
            )
        )

    with pytest.raises(WorkflowDowngradeProhibited, match="cannot continue under compact"):
        record_workflow_stage(
            RecordWorkflowStage(
                execution=upgraded,
                evidence=WorkflowStageEvidence(
                    stage=WorkflowStage.DRIVER_RED,
                    evidence_ids=("late-compact-work",),
                ),
            )
        )


@pytest.mark.parametrize(
    ("kind", "reason", "target"),
    [
        (
            RoleResultKind.ESCALATED,
            DriverReasonCode.SPECIFICATION_AMBIGUITY,
            WorkflowUpgradeTarget.CLARIFICATION,
        ),
        (
            RoleResultKind.ESCALATED,
            DriverReasonCode.ARCHITECTURAL_DECISION_REQUIRED,
            WorkflowUpgradeTarget.FULL,
        ),
        (
            RoleResultKind.ESCALATED,
            DriverReasonCode.AUTHORITATIVE_EVIDENCE_CONFLICT,
            WorkflowUpgradeTarget.CLARIFICATION,
        ),
        (
            RoleResultKind.BLOCKED,
            DriverReasonCode.INSUFFICIENT_WRITE_SCOPE,
            WorkflowUpgradeTarget.FULL,
        ),
    ],
)
def test_typed_driver_results_supply_ratchet_evidence_without_selecting_target(
    kind, reason, target
) -> None:
    upgraded = upgrade_workflow_for_driver_result(
        UpgradeWorkflowForDriverResult(
            execution=_compact_execution(),
            result=RoleExecutionResult(
                kind=kind,
                reason_code=reason,
                summary="The Driver cannot safely continue this compact stage.",
            ),
            evidence_ids=(f"role-result:{reason.value}",),
        )
    )

    assert upgraded.upgrade_history[0].target is target


def test_missing_context_remains_blocked_without_changing_the_recipe() -> None:
    execution = _compact_execution()

    unchanged = upgrade_workflow_for_driver_result(
        UpgradeWorkflowForDriverResult(
            execution=execution,
            result=RoleExecutionResult(
                kind=RoleResultKind.BLOCKED,
                reason_code=DriverReasonCode.MISSING_CONTEXT,
                summary="The required bounded context is unavailable.",
            ),
            evidence_ids=("role-result:missing-context",),
        )
    )

    assert unchanged == execution
