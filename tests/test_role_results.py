"""BTN-133 role-result policy and canonical-construction coverage."""
import pytest

from battalion.role_results import (
    DriverReasonCode,
    RoleResultKind,
    RoleResultRejected,
    RoleResultEvidenceReference,
    RoleResultSubmission,
    allowed_result_kinds,
    construct_role_result,
    submit_role_result,
)


def test_driver_red_and_green_admit_only_change_blocked_or_escalated():
    expected = {
        RoleResultKind.COMPLETED_WITH_CHANGE,
        RoleResultKind.BLOCKED,
        RoleResultKind.ESCALATED,
    }
    assert set(allowed_result_kinds("driver", "red")) == expected
    assert set(allowed_result_kinds("driver", "green")) == expected


def test_refactorer_is_the_only_initial_no_change_policy():
    result = construct_role_result(
        RoleResultSubmission(
            kind=RoleResultKind.COMPLETED_WITH_NO_CHANGE,
            summary="The implementation is already clear and local.",
        ),
        role="refactorer",
    )
    assert result.schema_version == "1"
    assert result.kind is RoleResultKind.COMPLETED_WITH_NO_CHANGE

    with pytest.raises(RoleResultRejected, match="not permitted"):
        construct_role_result(
            RoleResultSubmission(
                kind=RoleResultKind.COMPLETED_WITH_NO_CHANGE,
                summary="No change.",
            ),
            role="driver",
            mode="red",
        )


@pytest.mark.parametrize(
    "kind,reason",
    [
        (RoleResultKind.BLOCKED, DriverReasonCode.MISSING_CONTEXT),
        (RoleResultKind.BLOCKED, DriverReasonCode.INSUFFICIENT_WRITE_SCOPE),
        (RoleResultKind.ESCALATED, DriverReasonCode.SPECIFICATION_AMBIGUITY),
        (RoleResultKind.ESCALATED, DriverReasonCode.ARCHITECTURAL_DECISION_REQUIRED),
        (RoleResultKind.ESCALATED, DriverReasonCode.AUTHORITATIVE_EVIDENCE_CONFLICT),
    ],
)
def test_driver_non_mutating_reason_codes_follow_deterministic_mapping(kind, reason):
    result = construct_role_result(
        RoleResultSubmission(kind=kind, reason_code=reason, summary="Need human input."),
        role="driver",
        mode="green",
    )
    assert result.kind is kind
    assert result.reason_code is reason


def test_reason_kind_mismatch_and_unobserved_change_are_rejected():
    with pytest.raises(RoleResultRejected, match="requires one of"):
        construct_role_result(
            RoleResultSubmission(
                kind=RoleResultKind.BLOCKED,
                reason_code=DriverReasonCode.ARCHITECTURAL_DECISION_REQUIRED,
                summary="This is an architecture decision.",
            ),
            role="driver",
            mode="green",
        )
    with pytest.raises(RoleResultRejected, match="Battalion-observed"):
        construct_role_result(
            RoleResultSubmission(kind=RoleResultKind.COMPLETED_WITH_CHANGE),
            role="driver",
            mode="red",
        )


def test_submit_role_result_rejects_evidence_not_supplied_to_the_attempt():
    submission = RoleResultSubmission(
        kind=RoleResultKind.ESCALATED,
        reason_code=DriverReasonCode.SPECIFICATION_AMBIGUITY,
        summary="The plan and ticket specify incompatible outcomes.",
        evidence_refs=[
            RoleResultEvidenceReference(kind="artifact", reference="plan.md")
        ],
    )

    result = submit_role_result(
        submission,
        role="driver",
        mode="red",
        supplied_evidence_refs=[("artifact", "plan.md")],
    )
    assert result.evidence_refs[0].reference == "plan.md"

    with pytest.raises(RoleResultRejected, match="not supplied"):
        submit_role_result(
            submission,
            role="driver",
            mode="red",
            supplied_evidence_refs=[("workspace", "implementation roots")],
        )


def test_submission_rejects_unbounded_reasoning_fields():
    with pytest.raises(ValueError, match="extra_forbidden"):
        RoleResultSubmission.model_validate({
            "kind": "escalated",
            "reason_code": "specification-ambiguity",
            "summary": "The requirement permits incompatible public behavior.",
            "chain_of_thought": "This must never become durable evidence.",
        })
