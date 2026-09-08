"""Typed, Battalion-owned results for one bounded role execution (BTN-133).

Providers submit semantic arguments.  This module applies the role/mode policy
and constructs the stable persisted result used by execution evidence and
workflow routing.  It deliberately has no graph, filesystem, or transport
dependencies.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RoleResultKind(str, Enum):
    COMPLETED_WITH_CHANGE = "completed-with-change"
    COMPLETED_WITH_NO_CHANGE = "completed-with-no-change"
    BLOCKED = "blocked"
    ESCALATED = "escalated"


class DriverReasonCode(str, Enum):
    SPECIFICATION_AMBIGUITY = "specification-ambiguity"
    ARCHITECTURAL_DECISION_REQUIRED = "architectural-decision-required"
    MISSING_CONTEXT = "missing-context"
    AUTHORITATIVE_EVIDENCE_CONFLICT = "authoritative-evidence-conflict"
    INSUFFICIENT_WRITE_SCOPE = "insufficient-write-scope"


class RoleResultEvidenceReference(BaseModel):
    """A bounded pointer to supplied evidence; it never retains reasoning."""

    kind: Literal["state", "artifact", "workspace"]
    reference: str = Field(min_length=1, max_length=500)
    model_config = ConfigDict(extra="forbid")


class RoleResultSubmission(BaseModel):
    """Semantic arguments submitted by a role before canonical construction."""

    kind: RoleResultKind
    reason_code: DriverReasonCode | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=2000)
    evidence_refs: list[RoleResultEvidenceReference] = Field(default_factory=list, max_length=20)
    model_config = ConfigDict(extra="forbid")


class RoleExecutionResult(RoleResultSubmission):
    """Versioned canonical record constructed only by Battalion."""

    schema_version: Literal["1"] = "1"


class RoleResultRejected(ValueError):
    """A semantic result conflicts with Battalion's deterministic policy."""


@dataclass(frozen=True)
class RoleResultPolicy:
    allowed_kinds: frozenset[RoleResultKind]
    reasons_by_kind: dict[RoleResultKind, frozenset[DriverReasonCode]]


_DRIVER_POLICY = RoleResultPolicy(
    allowed_kinds=frozenset({
        RoleResultKind.COMPLETED_WITH_CHANGE,
        RoleResultKind.BLOCKED,
        RoleResultKind.ESCALATED,
    }),
    reasons_by_kind={
        RoleResultKind.BLOCKED: frozenset({
            DriverReasonCode.MISSING_CONTEXT,
            DriverReasonCode.INSUFFICIENT_WRITE_SCOPE,
        }),
        RoleResultKind.ESCALATED: frozenset({
            DriverReasonCode.SPECIFICATION_AMBIGUITY,
            DriverReasonCode.ARCHITECTURAL_DECISION_REQUIRED,
            DriverReasonCode.AUTHORITATIVE_EVIDENCE_CONFLICT,
        }),
    },
)

_REFACTORER_POLICY = RoleResultPolicy(
    allowed_kinds=frozenset({
        RoleResultKind.COMPLETED_WITH_CHANGE,
        RoleResultKind.COMPLETED_WITH_NO_CHANGE,
    }),
    reasons_by_kind={},
)


def policy_for(role: str, mode: str | None = None) -> RoleResultPolicy:
    """Return the admitted result policy for one concrete role attempt."""
    if role == "driver" and mode in {"red", "green"}:
        return _DRIVER_POLICY
    if role == "refactorer" and mode is None:
        return _REFACTORER_POLICY
    raise RoleResultRejected(
        f"No typed role-result policy is admitted for role={role!r}, mode={mode!r}"
    )


def allowed_result_kinds(role: str, mode: str | None = None) -> tuple[RoleResultKind, ...]:
    """Expose the exact allowed result kinds without relying on prompt memory."""
    return tuple(sorted(policy_for(role, mode).allowed_kinds, key=lambda item: item.value))


def construct_role_result(
    submission: RoleResultSubmission,
    *,
    role: str,
    mode: str | None = None,
    observed_artifact_count: int = 0,
) -> RoleExecutionResult:
    """Backward-compatible construction helper for role result callers."""

    return submit_role_result(
        submission,
        role=role,
        mode=mode,
        observed_artifact_count=observed_artifact_count,
    )


def validate_evidence_references(
    references: Iterable[RoleResultEvidenceReference],
    *,
    supplied_evidence_refs: Iterable[tuple[str, str]],
) -> None:
    """Require a persisted result to cite only evidence supplied to its attempt."""

    supplied = set(supplied_evidence_refs)
    unknown = sorted(
        f"{reference.kind}:{reference.reference}"
        for reference in references
        if (reference.kind, reference.reference) not in supplied
    )
    if unknown:
        raise RoleResultRejected(
            "role result cites evidence not supplied to this attempt: "
            + ", ".join(unknown)
        )


def submit_role_result(
    submission: RoleResultSubmission,
    *,
    role: str,
    mode: str | None = None,
    observed_artifact_count: int = 0,
    supplied_evidence_refs: Iterable[tuple[str, str]] | None = None,
) -> RoleExecutionResult:
    """Validate and normalize a role's semantic outcome for durable storage.

    ``COMPLETED_WITH_CHANGE`` is accepted only after Battalion has observed at
    least one scoped artifact write.  Non-mutating Driver outcomes require a
    bounded reason and operator-readable summary; result/reason mapping is
    policy rather than provider choice.
    """
    policy = policy_for(role, mode)
    kind = submission.kind
    if kind not in policy.allowed_kinds:
        raise RoleResultRejected(
            f"{role} {mode or ''} is not permitted to submit {kind.value}"
        )

    if kind is RoleResultKind.COMPLETED_WITH_CHANGE:
        if observed_artifact_count < 1:
            raise RoleResultRejected(
                "completed-with-change requires Battalion-observed artifact writes"
            )
        if submission.reason_code is not None:
            raise RoleResultRejected("completed-with-change cannot carry a reason code")
        result = RoleExecutionResult(**submission.model_dump())
        return _validate_supplied_evidence(result, supplied_evidence_refs)

    if kind is RoleResultKind.COMPLETED_WITH_NO_CHANGE:
        if submission.reason_code is not None:
            raise RoleResultRejected("completed-with-no-change cannot carry a reason code")
        if submission.summary is None:
            raise RoleResultRejected("completed-with-no-change requires a concise summary")
        result = RoleExecutionResult(**submission.model_dump())
        return _validate_supplied_evidence(result, supplied_evidence_refs)

    allowed_reasons = policy.reasons_by_kind.get(kind, frozenset())
    if submission.reason_code not in allowed_reasons:
        raise RoleResultRejected(
            f"{kind.value} requires one of: "
            f"{', '.join(sorted(reason.value for reason in allowed_reasons))}"
        )
    if submission.summary is None:
        raise RoleResultRejected(f"{kind.value} requires a concise summary")
    result = RoleExecutionResult(**submission.model_dump())
    return _validate_supplied_evidence(result, supplied_evidence_refs)


def _validate_supplied_evidence(
    result: RoleExecutionResult,
    supplied_evidence_refs: Iterable[tuple[str, str]] | None,
) -> RoleExecutionResult:
    if supplied_evidence_refs is not None:
        validate_evidence_references(
            result.evidence_refs,
            supplied_evidence_refs=supplied_evidence_refs,
        )
    return result
