"""Credential-free policy and real filesystem checks for target reconciliation."""

from datetime import datetime, timezone
import os
import subprocess

import pytest

from battalion.artifact_targets import ArtifactTargetContract
from battalion.artifact_target_reconciliation import (
    ArtifactTargetCurrentEvidence, ExactArtifactTargetEvidence, reconcile_artifact_targets,
)
from battalion.scope.artifact_target_paths import inspect_artifact_target_paths


def _directory_alias(link, destination):
    """Exercise Windows reparse points even without symlink privileges."""
    if os.name == "nt":
        link_arg = str(link).replace("'", "''")
        destination_arg = str(destination).replace("'", "''")
        subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"New-Item -ItemType Junction -Path '{link_arg}' -Target '{destination_arg}' -ErrorAction Stop",
        ], check=True, capture_output=True)
    else:
        link.symlink_to(destination, target_is_directory=True)


def _scenario(*, compact=False, targets=None):
    refs = [
        {"evidence_id": "work:1", "source": "work-item", "source_revision": "work-r1"},
        {"evidence_id": "spec:1", "source": "specification", "source_revision": "spec-r1"},
    ]
    contract = ArtifactTargetContract(
        project_id="bd4b6e64-25fd-408a-a747-9633a803f036",
        work_item_revision="work-r1", specification_revision="spec-r1",
        project_source_revision="source-r1", workflow_admission_decision_id="admission:1",
        architect_execution_id=None if compact else "architect:1",
        plan_artifact_digest=None if compact else "a" * 64,
        evidence_references=refs,
        targets=targets if targets is not None else [{
            "target_id": "greeting-test", "project_relative_path": "src/test_greeting.py",
            "assignments": [{
                "owner_role": "driver", "workflow_phase": "driver-red",
                "intended_operation": "create",
            }, {
                "owner_role": "driver", "workflow_phase": "driver-green",
                "intended_operation": "modify",
            }],
        }],
    )
    current = ArtifactTargetCurrentEvidence(
        **{field: getattr(contract, field) for field in (
            "project_id", "work_item_revision", "specification_revision",
            "project_source_revision", "workflow_admission_decision_id",
            "architect_execution_id", "plan_artifact_digest",
        )},
        recipe_id="compact-implementation-run" if compact else "full-implementation-run",
        recipe_version="1.0",
        references=[{**ref, "condition": "present", "authoritative": True} for ref in refs],
        exact_targets=[ExactArtifactTargetEvidence(reference=refs[0], targets=contract.targets)] if compact else [],
    )
    return contract, current


def _assess(base, contract, current, *, scope=None, case_sensitive=True, previous=None):
    paths = inspect_artifact_target_paths(
        contract, write_scope={"driver": ["src/"]} if scope is None else scope,
        base_dir=base, case_sensitive_paths=case_sensitive,
    )
    return reconcile_artifact_targets(
        contract, current=current, paths=paths, reconciliation_id="reconciliation:1",
        occurred_at=datetime(2026, 9, 6, tzinfo=timezone.utc), previous=previous,
    )


@pytest.mark.parametrize("compact", [False, True], ids=["architect-handoff", "authoritative-compact"])
def test_ready_reconciliation_is_deterministic_and_read_only(tmp_path, compact):
    contract, current = _scenario(compact=compact)
    before = contract.model_dump_json(), current.model_dump_json()
    first = _assess(tmp_path, contract, current)
    second = _assess(tmp_path, contract, current)
    assert first == second
    assert first.outcome == "ready"
    assert first.reason_codes == ()
    assert len(first.evidence_references) == 2
    assert not list(tmp_path.iterdir())
    assert before == (contract.model_dump_json(), current.model_dump_json())


@pytest.mark.parametrize("updates,reason", [
    pytest.param({"project_source_revision": None}, "missing-evidence", id="missing-source"),
    pytest.param({"project_source_revision": "latest"}, "missing-evidence", id="unpinned-source"),
    pytest.param({"project_source_revision": "source-r2"}, "stale-evidence", id="changed-source"),
    pytest.param({"work_item_revision": "work-r2"}, "stale-evidence", id="changed-work"),
    pytest.param({"specification_revision": "spec-r2"}, "stale-evidence", id="changed-spec"),
    pytest.param({"workflow_admission_decision_id": "admission:2"}, "stale-evidence", id="changed-admission"),
    pytest.param({"project_id": None}, "missing-evidence", id="missing-project"),
    pytest.param({"architect_execution_id": None}, "missing-evidence", id="missing-architect"),
    pytest.param({"plan_artifact_digest": "b" * 64}, "stale-evidence", id="changed-plan"),
    pytest.param({"recipe_version": "99.0"}, "incompatible-recipe", id="unknown-recipe"),
    pytest.param({"references": []}, "missing-evidence", id="missing-source-references"),
    pytest.param({"unresolved_reasons": ["ambiguous-targets"]}, "ambiguous-targets", id="unresolved-ambiguity"),
])
def test_missing_and_stale_current_evidence_fails_closed(tmp_path, updates, reason):
    contract, current = _scenario()
    changed = ArtifactTargetCurrentEvidence.model_validate({**current.model_dump(), **updates})
    result = _assess(tmp_path, contract, changed)
    assert result.outcome == "clarification-required"
    assert reason in result.reason_codes


@pytest.mark.parametrize("scope,reason", [
    pytest.param({"driver": ["src/"]}, None, id="legacy-scope"),
    pytest.param({"driver_red": ["src/"], "driver_green": ["src/"]}, None, id="phase-scope"),
    pytest.param({"driver": ["src/test_greeting.py"]}, None, id="exact-file"),
    pytest.param({"driver": ["src/"], "driver_red": []}, "out-of-scope", id="empty-phase-does-not-fallback"),
    pytest.param({"driver": ["tests/"]}, "out-of-scope", id="no-root-relative-reinterpretation"),
    pytest.param({"driver": ["src/test_other.py"]}, "out-of-scope", id="wrong-exact-file"),
    pytest.param({"driver": ["src/"], "unused": ["../escape/"]}, "out-of-scope", id="invalid-unused-scope"),
])
def test_exact_target_uses_existing_structural_scope(tmp_path, scope, reason):
    contract, current = _scenario()
    result = _assess(tmp_path, contract, current, scope=scope)
    assert result.outcome == ("ready" if reason is None else "clarification-required")
    if reason:
        assert reason in result.reason_codes


@pytest.mark.parametrize("mutation,reason", [
    pytest.param("absent", "missing-targets", id="scope-facts-cannot-invent-targets"),
    pytest.param("non-authoritative", "missing-targets", id="untrusted-target-source"),
    pytest.param("contradiction", "contradictory-evidence", id="conflicting-exact-target"),
    pytest.param("duplicate", "duplicate-targets", id="duplicate-source-target"),
    pytest.param("stale", "stale-evidence", id="stale-authoritative-source"),
    pytest.param("repository", "missing-targets", id="repository-hints-cannot-invent-targets"),
])
def test_compact_requires_consistent_authoritative_exact_targets(tmp_path, mutation, reason):
    contract, current = _scenario(compact=True)
    raw = current.model_dump(mode="json")
    if mutation == "absent":
        raw["exact_targets"] = []
    elif mutation == "non-authoritative":
        raw["references"][0]["authoritative"] = False
    elif mutation == "contradiction":
        raw["exact_targets"][0]["targets"][0]["project_relative_path"] = "test_greeting.py"
    elif mutation == "duplicate":
        raw["exact_targets"][0]["targets"].append(raw["exact_targets"][0]["targets"][0])
    elif mutation == "stale":
        raw["references"][0]["condition"] = "stale"
    elif mutation == "repository":
        raw["exact_targets"][0]["reference"] = {
            "evidence_id": "repository:1", "source": "repository", "source_revision": "source-r1",
        }
        raw["references"].append({
            **raw["exact_targets"][0]["reference"], "authoritative": True, "condition": "present",
        })
    result = _assess(tmp_path, contract, ArtifactTargetCurrentEvidence.model_validate(raw))
    assert result.outcome == "clarification-required"
    assert reason in result.reason_codes


def test_assignments_cannot_request_stages_outside_selected_recipe(tmp_path):
    contract, current = _scenario(compact=True)
    raw = contract.model_dump(exclude={"contract_id"})
    raw["targets"][0]["assignments"] = [{
        "owner_role": "refactorer", "workflow_phase": "refactor", "intended_operation": "modify",
    }]
    result = _assess(tmp_path, ArtifactTargetContract.model_validate(raw), current)
    assert result.outcome == "clarification-required"
    assert "incompatible-recipe" in result.reason_codes


def test_contract_must_cover_each_driver_phase(tmp_path):
    contract, current = _scenario()
    raw = contract.model_dump(exclude={"contract_id"})
    raw["targets"][0]["assignments"] = [raw["targets"][0]["assignments"][0]]
    result = _assess(tmp_path, ArtifactTargetContract.model_validate(raw), current)
    assert result.outcome == "clarification-required"
    assert "missing-targets" in result.reason_codes


def test_case_policy_rechecks_a_contract_created_with_case_sensitive_defaults(tmp_path):
    contract, current = _scenario()
    target = contract.targets[0].model_dump()
    contract, current = _scenario(targets=[target, {
        **target, "target_id": "other-test", "project_relative_path": "src/TEST_greeting.py",
    }])
    result = _assess(tmp_path, contract, current, case_sensitive=False)
    assert result.outcome == "clarification-required"
    assert "duplicate-targets" in result.reason_codes


@pytest.mark.parametrize("kind", ["directory-target", "file-parent"])
def test_targets_must_be_files_with_directory_parents(tmp_path, kind):
    contract, current = _scenario()
    if kind == "directory-target":
        (tmp_path / "src/test_greeting.py").mkdir(parents=True)
    else:
        (tmp_path / "src").write_text("not a directory", encoding="utf-8")
    result = _assess(tmp_path, contract, current)
    assert result.outcome == "clarification-required"
    assert "unsafe-path" in result.reason_codes


def test_resume_rechecks_scope_policy_and_exact_identity(tmp_path):
    contract, current = _scenario()
    original = _assess(tmp_path, contract, current)
    # Intended writes do not change the path-policy fingerprint.
    (tmp_path / "src").mkdir()
    (tmp_path / "src/test_greeting.py").write_text("assert False", encoding="utf-8")
    assert _assess(tmp_path, contract, current, previous=original).outcome == "ready"
    for kwargs in ({"scope": {"driver": ["src/", "tests/"]}}, {"case_sensitive": False}):
        result = _assess(tmp_path, contract, current, previous=original, **kwargs)
        assert result.outcome == "clarification-required"
        assert "stale-evidence" in result.reason_codes


def test_scope_order_is_not_a_change_but_explicit_phase_presence_is(tmp_path):
    contract, current = _scenario()
    first = _assess(tmp_path, contract, current, scope={"driver": ["src/", "tests/"]})
    reordered = _assess(tmp_path, contract, current, scope={"driver": ["tests/", "src/", "src/"]})
    assert first.write_scope_digest == reordered.write_scope_digest
    assert first.path_policy_digest == reordered.path_policy_digest
    changed = _assess(tmp_path, contract, current, scope={"driver": ["src/", "tests/"], "driver_green": []})
    assert changed.write_scope_digest != first.write_scope_digest


def test_missing_contract_produces_typed_clarification(tmp_path):
    _, current = _scenario()
    result = _assess(tmp_path, None, current)
    assert result.outcome == "clarification-required"
    assert "missing-targets" in result.reason_codes
    assert result.contract_id is None


@pytest.mark.parametrize("destination,reason", [
    pytest.param("actual", None, id="contained-link"),
    pytest.param("outside", "out-of-scope", id="escaping-scope-link"),
    pytest.param(".git", "unsafe-path", id="metadata-alias"),
    pytest.param("src", "out-of-scope", id="symlink-loop"),
])
def test_real_symlinks_follow_containment_and_metadata_policy(tmp_path, destination, reason):
    project = tmp_path / "project"
    project.mkdir()
    target = tmp_path / "outside" if destination == "outside" else project / destination
    if destination != "src":
        target.mkdir()
    if destination == "src":
        try:
            (project / "src").symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            pytest.skip("Windows symlink privilege is unavailable for loop scenario")
    else:
        _directory_alias(project / "src", target)
    contract, current = _scenario()
    result = _assess(project, contract, current)
    assert result.outcome == ("ready" if reason is None else "clarification-required")
    if reason:
        assert reason in result.reason_codes
    assert not (target / "test_greeting.py").is_file()


def test_retargeted_contained_symlink_invalidates_previous_path_evidence(tmp_path):
    for name in ("first", "second"):
        (tmp_path / name).mkdir()
    link = tmp_path / "src"
    _directory_alias(link, tmp_path / "first")
    contract, current = _scenario()
    original = _assess(tmp_path, contract, current)
    assert original.outcome == "ready"
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()
    _directory_alias(link, tmp_path / "second")
    result = _assess(tmp_path, contract, current, previous=original)
    assert result.outcome == "clarification-required"
    assert "stale-evidence" in result.reason_codes


def test_path_inspection_cannot_be_reused_for_another_contract(tmp_path):
    contract, current = _scenario()
    paths = inspect_artifact_target_paths(
        contract, write_scope={"driver": ["src/"]}, base_dir=tmp_path, case_sensitive_paths=True,
    )
    changed = ArtifactTargetContract.model_validate({
        **contract.model_dump(exclude={"contract_id"}), "supersedes_contract_id": contract.contract_id,
    })
    result = reconcile_artifact_targets(
        changed, current=current, paths=paths, reconciliation_id="reconciliation:2",
        occurred_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )
    assert result.outcome == "clarification-required"
    assert "stale-evidence" in result.reason_codes
