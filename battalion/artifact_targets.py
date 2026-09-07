"""Provider-neutral artifact-target contracts (ADR-0038, BTN-194).

These frozen values describe evidence, never write authority. Version 1.0 of
each aggregate owns its nested schema. Validation is lexical and performs no
filesystem IO; application reconciliation must separately check symlinks,
source revisions, selected recipe, and independently authorized write scopes.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
from pathlib import PureWindowsPath
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo,
    field_validator, model_validator,
)

from battalion.workflow_admission import AdmissionEvidenceSource
from battalion.workflow_recipes import WorkflowStage


Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=200,
                           pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
]
Revision = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True,
                           min_length=1, max_length=1000)
]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_PROTECTED_COMPONENTS = frozenset({".battalion", ".git", ".hg", ".svn", ".bzr"})
_GENERATED_PLAN_MARKERS = (
    "<!-- BEGIN GENERATED:artifact-targets -->",
    "<!-- END GENERATED:artifact-targets -->",
)


def normalize_target_path(value: str) -> str:
    """Normalize exact relative file paths identically on every host.

    Backslashes become slashes and repeated internal separators collapse.
    Dot components are rejected, not resolved. Windows aliases/devices are
    rejected even on POSIX so moving a contract cannot reinterpret its path.
    """
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError("target path must be a non-empty string of at most 1000 characters")
    path = value.replace("\\", "/")
    parts = path.split("/")
    reserved = (
        ntpath.isreserved(path) if hasattr(ntpath, "isreserved") else
        any(PureWindowsPath(part).is_reserved() for part in parts)
    )
    if (
        PureWindowsPath(path).anchor or path.endswith("/")
        or any(part in {".", ".."} for part in parts)
        or any(ord(char) < 32 or char in '<>:"|?*[]' for char in path)
        or reserved
        or any(part.endswith((".", " ")) for part in parts)
        or any(part.casefold() in _PROTECTED_COMPONENTS for part in parts)
    ):
        raise ValueError(f"unsafe exact project-relative target path: {value!r}")
    return "/".join(part for part in parts if part)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always",
    )


class ArtifactTargetEvidenceReference(_FrozenModel):
    """A bounded revision-pinned reference, not embedded source contents.

    Reuses admission's authoritative-source vocabulary; Architect execution
    and plan provenance have explicit fields on the sealed contract.
    """

    evidence_id: Identifier
    source: AdmissionEvidenceSource
    source_revision: Revision

    @field_validator("source_revision")
    @classmethod
    def reject_unpinned_revision(cls, value: str) -> str:
        if value.casefold() == "latest":
            raise ValueError("evidence requires an explicit revision, not 'latest'")
        return value


def _order_evidence(
    values: tuple[ArtifactTargetEvidenceReference, ...],
) -> tuple[ArtifactTargetEvidenceReference, ...]:
    ids = [value.evidence_id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError("evidence IDs must be unique within a reference list")
    return tuple(sorted(values, key=lambda value: value.evidence_id))


class ArtifactTargetAssignment(_FrozenModel):
    """An intended mutation by a writing role, not a grant of permission."""

    owner_role: Literal["architect", "driver", "refactorer"]
    workflow_phase: WorkflowStage
    intended_operation: Literal["create", "modify", "delete"]

    @model_validator(mode="after")
    def validate_role_phase(self) -> Self:
        allowed = {
            "architect": {WorkflowStage.ARCHITECTURE},
            "driver": {WorkflowStage.DRIVER_RED, WorkflowStage.DRIVER_GREEN},
            "refactorer": {WorkflowStage.REFACTOR},
        }
        if self.workflow_phase not in allowed[self.owner_role]:
            raise ValueError("target assignment phase must belong to its writing role")
        return self


class ArtifactTarget(_FrozenModel):
    target_id: Identifier
    project_relative_path: str = Field(min_length=1, max_length=1000, strict=True)
    assignments: tuple[ArtifactTargetAssignment, ...] = Field(min_length=1, max_length=20)
    evidence_references: tuple[ArtifactTargetEvidenceReference, ...] = Field(
        default=(), max_length=50,
    )

    _normalize_path = field_validator("project_relative_path")(normalize_target_path)
    _canonical_evidence = field_validator("evidence_references")(_order_evidence)

    @field_validator("assignments")
    @classmethod
    def order_assignments(
        cls, values: tuple[ArtifactTargetAssignment, ...],
    ) -> tuple[ArtifactTargetAssignment, ...]:
        if len(values) != len(set(values)):
            raise ValueError("target assignments must be unique")
        phases = [value.workflow_phase for value in values]
        if len(phases) != len(set(phases)):
            raise ValueError("a target cannot have competing operations in one phase")
        return tuple(sorted(values, key=lambda value: (
            value.owner_role, value.workflow_phase.value, value.intended_operation,
        )))


class _TargetCollection(_FrozenModel):
    targets: tuple[ArtifactTarget, ...] = Field(min_length=1, max_length=100)

    @field_validator("targets")
    @classmethod
    def validate_targets(
        cls, targets: tuple[ArtifactTarget, ...], info: ValidationInfo,
    ) -> tuple[ArtifactTarget, ...]:
        """Call model_validate(..., context={"case_sensitive_paths": False})
        for a case-insensitive project. The deterministic default is sensitive;
        neither the current working directory nor host OS supplies policy.
        """
        case_sensitive = (info.context or {}).get("case_sensitive_paths", True)
        if not isinstance(case_sensitive, bool):
            raise ValueError("case_sensitive_paths policy must be a boolean")
        ids: dict[str, str] = {}
        paths: set[str] = set()
        for target in targets:
            path = target.project_relative_path
            if target.target_id in ids:
                raise ValueError(
                    f"duplicate target ID {target.target_id!r}: "
                    f"{ids[target.target_id]!r} and {path!r}"
                )
            ids[target.target_id] = path
            key = path if case_sensitive else path.casefold()
            if key in paths:
                raise ValueError(f"duplicate normalized target path: {path!r}")
            paths.add(key)
        return tuple(sorted(targets, key=lambda target: target.target_id))


class ArtifactTargetContract(_TargetCollection):
    """Canonical evidence sealed with caller-supplied revisions/provenance.

    Omit contract_id to calculate it; a supplied ID must match normalized
    content. Targets, assignments, and evidence lists are canonically ordered.
    No approval, mutable Run state, or provider reasoning enters the digest.
    """

    contract_version: Literal["1.0"] = "1.0"
    contract_id: Digest = ""  # calculated after content validation
    project_id: UUID
    work_item_revision: Revision
    specification_revision: Revision
    project_source_revision: Revision
    workflow_admission_decision_id: Revision
    architect_execution_id: Revision | None = None
    plan_artifact_digest: Digest | None = None
    supersedes_contract_id: Digest | None = None
    evidence_references: tuple[ArtifactTargetEvidenceReference, ...] = Field(
        min_length=1, max_length=50,
    )

    _canonical_evidence = field_validator("evidence_references")(_order_evidence)

    @field_validator("work_item_revision", "specification_revision", "project_source_revision")
    @classmethod
    def reject_unpinned_revision(cls, value: str) -> str:
        if value.casefold() == "latest":
            raise ValueError("contract requires explicit revisions, not 'latest'")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude={"contract_id"}),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.contract_id and self.contract_id != expected:
            raise ValueError("contract_id does not match canonical contract content")
        object.__setattr__(self, "contract_id", expected)
        return self


class ArchitectImplementationStep(_FrozenModel):
    """Ordered narrative referring to IDs; no path-definition field exists."""

    description: str = Field(min_length=1, max_length=4000, strict=True)
    target_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)

    @field_validator("description")
    @classmethod
    def reject_blank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("implementation step description cannot be blank")
        if any(marker in value for marker in _GENERATED_PLAN_MARKERS):
            raise ValueError("implementation step cannot impersonate generated target content")
        return value

    @field_validator("target_ids")
    @classmethod
    def order_target_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("step target IDs must be unique")
        return tuple(sorted(values))


class ArchitectHandoffCandidate(_TargetCollection):
    """Model-produced evidence, not a sealed contract or persisted handoff."""

    handoff_version: Literal["1.0"] = "1.0"
    plan_markdown: str = Field(min_length=1, max_length=65536, strict=True)
    implementation_steps: tuple[ArchitectImplementationStep, ...] = Field(
        min_length=1, max_length=100,
    )

    @field_validator("plan_markdown")
    @classmethod
    def reject_blank_plan(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("plan Markdown cannot be blank")
        if any(marker in value for marker in _GENERATED_PLAN_MARKERS):
            raise ValueError("plan Markdown cannot impersonate generated target content")
        return value

    @model_validator(mode="after")
    def validate_step_references(self) -> Self:
        known = {target.target_id for target in self.targets}
        for step in self.implementation_steps:
            unknown = set(step.target_ids) - known
            if unknown:
                raise ValueError(f"implementation step references unknown targets: {sorted(unknown)}")
        return self
