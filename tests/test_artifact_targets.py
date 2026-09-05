"""Credential-free domain tests for the ADR-0038 artifact-target contract."""

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from battalion.artifact_targets import (
    ArchitectHandoffCandidate,
    ArtifactTarget,
    ArtifactTargetAssignment,
    ArtifactTargetContract,
    ArtifactTargetEvidenceReference,
    normalize_target_path,
)


def assignment(**updates):
    return {"owner_role": "driver", "workflow_phase": "driver-red",
            "intended_operation": "create", **updates}


def target(target_id="greeting-test", path="src/test_greeting.py", **updates):
    return {"target_id": target_id, "project_relative_path": path,
            "assignments": [assignment()], **updates}


def reference(evidence_id="work:1", **updates):
    return {"evidence_id": evidence_id, "source": "work-item",
            "source_revision": "work-r1", **updates}


def contract_payload(**updates):
    return {
        "project_id": "bd4b6e64-25fd-408a-a747-9633a803f036",
        "work_item_revision": "work-r1",
        "specification_revision": "spec-r1",
        "project_source_revision": "source-r1",
        "workflow_admission_decision_id": "admission:1",
        "evidence_references": [reference()],
        "targets": [target()],
        **updates,
    }


def candidate_payload(**updates):
    return {
        "plan_markdown": "# Greeting plan\n\nImplement the agreed behavior.",
        "targets": [target()],
        "implementation_steps": [{"description": "Add the failing greeting test.",
                                  "target_ids": ["greeting-test"]}],
        **updates,
    }


def test_contract_identity_matches_canonical_json_and_round_trips():
    contract = ArtifactTargetContract.model_validate(contract_payload())
    canonical = json.dumps(
        contract.model_dump(mode="json", exclude={"contract_id"}),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    assert contract.contract_version == "1.0"
    assert contract.contract_id == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert ArtifactTargetContract.model_validate_json(contract.model_dump_json()) == contract


def test_normalization_and_unordered_collections_reproduce_identity():
    payload = contract_payload(
        evidence_references=[reference("work:2"), reference("work:1")],
        targets=[
            target("greeting-test", "src//test_greeting.py", assignments=[
                assignment(owner_role="refactorer", workflow_phase="refactor",
                           intended_operation="modify"), assignment(),
            ], evidence_references=[reference("work:2"), reference("work:1")]),
            target("greeting-source", "src/greeting.py", assignments=[
                assignment(workflow_phase="driver-green"),
            ]),
        ],
    )
    reordered = deepcopy(payload)
    reordered["evidence_references"].reverse()
    reordered["targets"].reverse()
    reordered["targets"][1]["assignments"].reverse()
    reordered["targets"][1]["evidence_references"].reverse()
    reordered["targets"][1]["project_relative_path"] = "src\\test_greeting.py"
    first = ArtifactTargetContract.model_validate(payload)
    second = ArtifactTargetContract.model_validate(reordered)
    assert first == second
    assert first.targets[1].project_relative_path == "src/test_greeting.py"


@pytest.mark.parametrize("updates", [
    {"project_id": "528f1a80-8268-4ecf-b43d-03a0a7c038db"},
    {"work_item_revision": "work-r2"},
    {"specification_revision": "spec-r2"},
    {"project_source_revision": "source-r2"},
    {"workflow_admission_decision_id": "admission:2"},
    {"architect_execution_id": "execution:1"},
    {"plan_artifact_digest": "a" * 64},
    {"supersedes_contract_id": "b" * 64},
    {"evidence_references": [reference(source_revision="work-r2")]},
    {"evidence_references": [reference("work:2")]},
    {"evidence_references": [reference(source="specification")]},
    {"targets": [target(path="tests/test_greeting.py")]},
    {"targets": [target(target_id="other-test")]},
    {"targets": [target(assignments=[assignment(intended_operation="modify")])]},
    {"targets": [target(assignments=[assignment(workflow_phase="driver-green")])]},
    {"targets": [target(evidence_references=[reference()])]},
])
def test_every_material_change_invalidates_identity(updates):
    original = ArtifactTargetContract.model_validate(contract_payload())
    changed = ArtifactTargetContract.model_validate(contract_payload(**updates))
    assert changed.contract_id != original.contract_id
    with pytest.raises(ValidationError, match="contract_id does not match"):
        ArtifactTargetContract.model_validate(
            contract_payload(**updates, contract_id=original.contract_id)
        )


@pytest.mark.parametrize("path", [
    "", " ", ".", "..", "/src/a.py", "C:/src/a.py", "C:a.py",
    "\\\\server\\share\\a.py", "//server/share/a.py", "\\src\\a.py",
    "src/./a.py", "src/../a.py", "../a.py", "src\\..\\a.py", "src/",
    "src/*.py", "src/a?.py", "src/[ab].py", "src/a\x00.py", "src/a\n.py",
    ".battalion/run.json", "src/.BATTALION/state.json", ".git/config",
    "src/.GIT", ".hg/store", ".svn/entries", ".bzr/branch",
    "src/a.py:stream", "src/a.py.", "src/a.py ", "NUL", "src/CON.py",
    "a" * 1001,
])
def test_unsafe_paths_are_rejected_on_every_host(path):
    with pytest.raises(ValueError):
        normalize_target_path(path)
    with pytest.raises(ValidationError):
        ArtifactTarget.model_validate(target(path=path))


def test_valid_unicode_paths_and_separator_normalization():
    assert normalize_target_path("src\\nested//greeting.py") == "src/nested/greeting.py"
    assert normalize_target_path("docs/日本語.md") == "docs/日本語.md"


@pytest.mark.parametrize("targets, message", [
    ([target(), target()], "duplicate target ID"),
    ([target(), target(path="test_greeting.py")], "duplicate target ID"),
    ([target(), target("second", "src\\test_greeting.py")], "duplicate normalized"),
    ([target(), target("second", "src//test_greeting.py")], "duplicate normalized"),
])
@pytest.mark.parametrize("model, payload", [
    (ArtifactTargetContract, contract_payload), (ArchitectHandoffCandidate, candidate_payload),
])
def test_duplicate_targets_and_logical_path_conflicts_fail(model, payload, targets, message):
    with pytest.raises(ValidationError, match=message) as caught:
        model.model_validate(payload(targets=targets))
    if targets[1]["project_relative_path"] == "test_greeting.py":
        assert "src/test_greeting.py" in str(caught.value)
        assert "'test_greeting.py'" in str(caught.value)


@pytest.mark.parametrize("model, payload", [
    (ArtifactTargetContract, contract_payload), (ArchitectHandoffCandidate, candidate_payload),
])
def test_case_collisions_follow_explicit_project_policy(model, payload):
    data = payload(targets=[target(), target("second", "src/TEST_greeting.py")])
    sensitive = model.model_validate(data, context={"case_sensitive_paths": True})
    assert len(sensitive.targets) == 2
    with pytest.raises(ValidationError, match="duplicate normalized"):
        model.model_validate(data, context={"case_sensitive_paths": False})
    # Passing an already constructed instance must not bypass a stricter policy.
    with pytest.raises(ValidationError, match="duplicate normalized"):
        model.model_validate(sensitive, context={"case_sensitive_paths": False})
    with pytest.raises(ValidationError, match="must be a boolean"):
        model.model_validate(data, context={"case_sensitive_paths": "false"})


@pytest.mark.parametrize("assignments, message", [
    ([assignment(), assignment()], "assignments must be unique"),
    ([assignment(), assignment(intended_operation="delete")], "competing operations"),
    ([assignment(owner_role="reviewer")], "literal_error"),
    ([assignment(workflow_phase="review-red")], "phase must belong"),
    ([assignment(workflow_phase="driver")], "enum"),
    ([assignment(intended_operation="read")], "literal_error"),
    ([assignment(owner_role="architect")], "phase must belong"),
])
def test_assignments_have_closed_consistent_roles_phases_and_operations(assignments, message):
    with pytest.raises(ValidationError, match=message):
        ArtifactTarget.model_validate(target(assignments=assignments))


@pytest.mark.parametrize("field", [
    "work_item_revision", "specification_revision", "project_source_revision",
])
@pytest.mark.parametrize("value", [None, "", "  ", "latest", "LATEST"])
def test_revisions_must_be_explicit_and_nonempty(field, value):
    with pytest.raises(ValidationError):
        ArtifactTargetContract.model_validate(contract_payload(**{field: value}))


def test_nested_values_are_frozen_and_unknown_fields_are_rejected():
    contract = ArtifactTargetContract.model_validate(contract_payload())
    for model, field, value in [
        (contract, "project_source_revision", "other"),
        (contract.targets[0], "project_relative_path", "other.py"),
        (contract.targets[0].assignments[0], "intended_operation", "delete"),
        (contract.evidence_references[0], "source_revision", "other"),
    ]:
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, value)
    assert isinstance(contract.targets, tuple)
    assert isinstance(contract.targets[0].assignments, tuple)
    with pytest.raises(ValidationError, match="Extra inputs"):
        ArtifactTargetContract.model_validate(contract_payload(write_scope={"driver": ["/"]}))
    with pytest.raises(ValidationError, match="Extra inputs"):
        ArtifactTargetAssignment.model_validate(assignment(write_scope=["/"]))


def test_evidence_is_bounded_revision_pinned_and_unambiguous():
    with pytest.raises(ValidationError):
        ArtifactTargetEvidenceReference.model_validate(reference(source_revision="latest"))
    with pytest.raises(ValidationError):
        ArtifactTargetEvidenceReference.model_validate(reference(source="llm-reasoning"))
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        ArtifactTargetContract.model_validate(contract_payload(
            evidence_references=[reference(), reference(source_revision="work-r2")],
        ))
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        ArtifactTarget.model_validate(target(evidence_references=[reference(), reference()]))


@pytest.mark.parametrize("updates", [
    {"handoff_version": "2.0"}, {"plan_markdown": "  "},
    {"plan_markdown": "a" * 65537}, {"targets": []}, {"implementation_steps": []},
    {"implementation_steps": [{"description": "Do it", "target_ids": ["unknown"]}]},
    {"implementation_steps": [{"description": "Do it", "target_ids": [
        "greeting-test", "greeting-test",
    ]}]},
    {"implementation_steps": [{"description": "  ", "target_ids": ["greeting-test"]}]},
    {"implementation_steps": [{"description": "Do it", "target_ids": ["greeting-test"],
                               "project_relative_path": "test_greeting.py"}]},
    {"implementation_steps": [{"description": "Do it", "target_ids": [
        {"target_id": "greeting-test", "project_relative_path": "test_greeting.py"},
    ]}]},
    {"contract_id": "a" * 64}, {"write_scope": {"architect": ["src/"]}},
])
def test_invalid_candidate_or_step_path_redefinition_is_rejected(updates):
    with pytest.raises(ValidationError):
        ArchitectHandoffCandidate.model_validate(candidate_payload(**updates))


def test_valid_candidate_retains_ordered_steps_and_non_authoritative_narrative():
    data = candidate_payload(implementation_steps=[
        {"description": "First write the test.", "target_ids": ["greeting-test"]},
        {"description": "Then verify the failure.", "target_ids": ["greeting-test"]},
    ])
    candidate = ArchitectHandoffCandidate.model_validate(data)
    assert candidate.implementation_steps[0].description == "First write the test."
    assert candidate.plan_markdown == data["plan_markdown"]
    assert ArchitectHandoffCandidate.model_validate_json(candidate.model_dump_json()) == candidate
    with pytest.raises(ValidationError, match="frozen"):
        candidate.implementation_steps[0].description = "Replaced"


def test_domain_construction_does_not_resolve_filesystem_or_mutate_inputs(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("domain contract must not access the filesystem")
    monkeypatch.setattr("pathlib.Path.resolve", forbidden)
    original = contract_payload()
    snapshot = deepcopy(original)
    ArtifactTargetContract.model_validate(original)
    assert original == snapshot
