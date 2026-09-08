"""Deterministic handoff policy over explicit evidence, without filesystem IO.

The application must collect current evidence and a fresh path inspection.
Neither constructing these inputs nor reconciling them dispatches a role or
authorizes a human action. Production gate wiring is a separate responsibility.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from battalion.artifact_targets import (
    ArtifactTarget, ArtifactTargetContract, ArtifactTargetEvidenceReference, Digest, Revision,
)
from battalion.artifact_target_state import ArtifactTargetReasonCode as Reason
from battalion.artifact_target_state import ArtifactTargetReconciliation
from battalion.workflow_admission import (
    AdmissionEvidenceCondition as Condition, AdmissionEvidenceReference,
    AdmissionEvidenceSource as Source,
)
from battalion.workflow_recipes import (
    DEFAULT_WORKFLOW_RECIPE_REGISTRY, UnknownWorkflowRecipe,
    WorkflowRecipeRegistry, WorkflowStage,
)


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class ExactArtifactTargetEvidence(_Evidence):
    """Exact targets supplied by a source; authority is checked, never assumed."""

    reference: ArtifactTargetEvidenceReference
    targets: tuple[ArtifactTarget, ...] = Field(min_length=1, max_length=100)


class ArtifactTargetCurrentEvidence(_Evidence):
    project_id: UUID | None = None
    work_item_revision: Revision | None = None
    specification_revision: Revision | None = None
    project_source_revision: Revision | None = None
    workflow_admission_decision_id: Revision | None = None
    recipe_id: Revision
    recipe_version: Revision
    architect_execution_id: Revision | None = None
    plan_artifact_digest: Digest | None = None
    references: tuple[AdmissionEvidenceReference, ...] = Field(default=(), max_length=500)
    exact_targets: tuple[ExactArtifactTargetEvidence, ...] = Field(default=(), max_length=50)
    unresolved_reasons: tuple[Reason, ...] = Field(default=(), max_length=20)


class ArtifactTargetPathInspection(_Evidence):
    """Read-only adapter output tied to the exact inspected contract."""

    contract_id: Digest | None = None
    checked_target_ids: tuple[str, ...] = Field(default=(), max_length=100)
    write_scope_digest: Digest
    path_policy_digest: Digest
    case_sensitive_paths: bool = Field(strict=True)
    reason_codes: tuple[Reason, ...] = Field(default=(), max_length=20)


def reconcile_artifact_targets(
    contract: ArtifactTargetContract | None,
    *,
    current: ArtifactTargetCurrentEvidence,
    paths: ArtifactTargetPathInspection,
    reconciliation_id: str,
    occurred_at: datetime,
    previous: ArtifactTargetReconciliation | None = None,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> ArtifactTargetReconciliation:
    """Assess exact identities and targets; never substitute a newer contract.

    Supply ``previous`` when revalidating an existing authorization. Its exact
    recipe/scope/path snapshot must still match, even if a broader new scope
    would otherwise admit the same paths. A new correction is assessed afresh
    by the human-action boundary, not treated as a transparent resume.
    """
    reasons = set(current.unresolved_reasons) | set(paths.reason_codes)
    try:
        recipe = registry.resolve(current.recipe_id, current.recipe_version)
    except UnknownWorkflowRecipe:
        recipe = None
        reasons.add(Reason.INCOMPATIBLE_RECIPE)

    reference_index: dict[str, AdmissionEvidenceReference] = {}
    for reference in current.references:
        if reference.evidence_id in reference_index:
            reasons.add(Reason.CONTRADICTORY_EVIDENCE)
        reference_index[reference.evidence_id] = reference

    def check_reference(reference: ArtifactTargetEvidenceReference) -> bool:
        observed = reference_index.get(reference.evidence_id)
        if observed is None or not observed.authoritative or observed.condition == Condition.MISSING:
            reasons.add(Reason.MISSING_EVIDENCE)
            return False
        if observed.condition == Condition.CONTRADICTORY:
            reasons.add(Reason.CONTRADICTORY_EVIDENCE)
            return False
        if observed.condition == Condition.STALE or (
            observed.source != reference.source or observed.source_revision != reference.source_revision
        ):
            reasons.add(Reason.STALE_EVIDENCE)
            return False
        return True

    retained_references: set[ArtifactTargetEvidenceReference] = set()
    if contract is None:
        reasons.add(Reason.MISSING_TARGETS)
    else:
        for field in (
            "project_id", "work_item_revision", "specification_revision",
            "project_source_revision", "workflow_admission_decision_id",
        ):
            value = getattr(current, field)
            if value is None or (isinstance(value, str) and value.casefold() == "latest"):
                reasons.add(Reason.MISSING_EVIDENCE)
            elif value != getattr(contract, field):
                reasons.add(Reason.STALE_EVIDENCE)
        if paths.contract_id != contract.contract_id:
            reasons.add(Reason.STALE_EVIDENCE)
        if sorted(paths.checked_target_ids) != sorted(target.target_id for target in contract.targets):
            reasons.add(Reason.MISSING_EVIDENCE)

        retained_references.update(contract.evidence_references)
        for target in contract.targets:
            retained_references.update(target.evidence_references)
            if recipe is not None and any(
                assignment.workflow_phase not in recipe.stages for assignment in target.assignments
            ):
                reasons.add(Reason.INCOMPATIBLE_RECIPE)
        for reference in retained_references:
            check_reference(reference)
        for source, revision in (
            (Source.WORK_ITEM, contract.work_item_revision),
            (Source.SPECIFICATION, contract.specification_revision),
        ):
            if not any(ref.source == source and ref.source_revision == revision for ref in retained_references):
                reasons.add(Reason.MISSING_EVIDENCE)

        if not any(a.owner_role == "driver" for t in contract.targets for a in t.assignments):
            reasons.add(Reason.MISSING_TARGETS)
        if recipe is not None:
            assigned_phases = {a.workflow_phase for t in contract.targets for a in t.assignments}
            required_driver_phases = set(recipe.stages) & {
                WorkflowStage.DRIVER_RED, WorkflowStage.DRIVER_GREEN,
            }
            if required_driver_phases - assigned_phases:
                reasons.add(Reason.MISSING_TARGETS)
        if recipe is not None and WorkflowStage.ARCHITECTURE in recipe.stages:
            if current.architect_execution_id is None or current.plan_artifact_digest is None:
                reasons.add(Reason.MISSING_EVIDENCE)
            if (
                contract.architect_execution_id != current.architect_execution_id
                or contract.plan_artifact_digest != current.plan_artifact_digest
            ):
                reasons.add(Reason.STALE_EVIDENCE)

        authoritative: dict[str, ArtifactTarget] = {}
        for supplied in current.exact_targets:
            ref = supplied.reference
            # Admission scope facts, repository hints and model advice cannot
            # invent compact targets. Only these two authoritative sources can.
            observed = reference_index.get(ref.evidence_id)
            if ref.source not in {Source.WORK_ITEM, Source.SPECIFICATION} or (
                observed is None or not observed.authoritative
            ):
                continue
            retained_references.add(ref)
            expected_revision = (current.work_item_revision if ref.source == Source.WORK_ITEM
                                 else current.specification_revision)
            if ref.source_revision != expected_revision:
                reasons.add(Reason.STALE_EVIDENCE)
            if not check_reference(ref):
                continue
            source_ids: set[str] = set()
            source_paths: set[str] = set()
            for target in supplied.targets:
                path_key = (target.project_relative_path if paths.case_sensitive_paths
                            else target.project_relative_path.casefold())
                if target.target_id in source_ids or path_key in source_paths:
                    reasons.add(Reason.DUPLICATE_TARGETS)
                source_ids.add(target.target_id)
                source_paths.add(path_key)
                existing = authoritative.get(target.target_id)
                if existing is not None and _target_meaning(existing) != _target_meaning(target):
                    reasons.add(Reason.CONTRADICTORY_EVIDENCE)
                authoritative[target.target_id] = target
        if set(authoritative) - {target.target_id for target in contract.targets}:
            reasons.add(Reason.MISSING_TARGETS)
        authoritative_paths = [t.project_relative_path if paths.case_sensitive_paths
                               else t.project_relative_path.casefold() for t in authoritative.values()]
        if len(authoritative_paths) != len(set(authoritative_paths)):
            reasons.add(Reason.DUPLICATE_TARGETS)
        for target in contract.targets:
            exact = authoritative.get(target.target_id)
            if exact is not None and _target_meaning(exact) != _target_meaning(target):
                reasons.add(Reason.CONTRADICTORY_EVIDENCE)
            if recipe is not None and WorkflowStage.ARCHITECTURE not in recipe.stages and exact is None:
                reasons.add(Reason.MISSING_TARGETS)

    if previous is not None and (
        previous.contract_id != (contract.contract_id if contract else None)
        or previous.recipe_id != current.recipe_id
        or previous.recipe_version != current.recipe_version
        or previous.project_source_revision != current.project_source_revision
        or previous.write_scope_digest != paths.write_scope_digest
        or previous.path_policy_digest != paths.path_policy_digest
    ):
        reasons.add(Reason.STALE_EVIDENCE)
    return ArtifactTargetReconciliation(
        reconciliation_id=reconciliation_id, occurred_at=occurred_at,
        contract_id=contract.contract_id if contract else None,
        outcome="clarification-required" if reasons else "ready",
        reason_codes=tuple(sorted(reasons, key=lambda reason: reason.value)),
        recipe_id=current.recipe_id, recipe_version=current.recipe_version,
        project_source_revision=current.project_source_revision,
        write_scope_digest=paths.write_scope_digest, path_policy_digest=paths.path_policy_digest,
        evidence_references=tuple(sorted(retained_references, key=lambda ref: (
            ref.evidence_id, ref.source.value, ref.source_revision,
        ))),
    )


def _target_meaning(target: ArtifactTarget) -> tuple:
    return target.project_relative_path, target.assignments
