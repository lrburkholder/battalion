"""Immutable target history and corruption rejection before runtime wiring."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from battalion.artifact_targets import ArtifactTargetContract
from battalion.artifact_target_state import (
    ArtifactTargetCorrection,
    ArtifactTargetHandoffRecord,
    ArtifactTargetReconciliation,
)


def _corrected_history():
    original = ArtifactTargetContract(
        project_id="bd4b6e64-25fd-408a-a747-9633a803f036",
        work_item_revision="work-r1", specification_revision="spec-r1",
        project_source_revision="source-r1", workflow_admission_decision_id="admission:1",
        evidence_references=[{
            "evidence_id": "work:1", "source": "work-item", "source_revision": "work-r1",
        }],
        targets=[{
            "target_id": "greeting-test", "project_relative_path": "src/test_greeting.py",
            "assignments": [{
                "owner_role": "driver", "workflow_phase": "driver-red",
                "intended_operation": "create",
            }],
        }],
    )
    payload = original.model_dump(exclude={"contract_id"})
    payload["targets"][0]["project_relative_path"] = "tests/test_greeting.py"
    corrected = ArtifactTargetContract.model_validate({
        **payload, "supersedes_contract_id": original.contract_id,
    })
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    result = ArtifactTargetReconciliation(
        reconciliation_id="reconciliation:1", contract_id=original.contract_id,
        occurred_at=now, outcome="clarification-required", reason_codes=["out-of-scope"],
        recipe_id="full-implementation-run", recipe_version="1.0",
        project_source_revision="source-r1", write_scope_digest="a" * 64,
        path_policy_digest="b" * 64,
    )
    ready = ArtifactTargetReconciliation.model_validate({
        **result.model_dump(), "reconciliation_id": "reconciliation:2",
        "contract_id": corrected.contract_id, "outcome": "ready", "reason_codes": [],
    })
    return ArtifactTargetHandoffRecord(
        contracts=[original, corrected], reconciliations=[result, ready],
        corrections=[ArtifactTargetCorrection(
            action_id="correction:1", actor_id="8fd5f40b-37dd-4ab3-8f7d-938a30fe3d46",
            occurred_at=now, action="approve-correction",
            previous_contract_id=original.contract_id, corrected_contract_id=corrected.contract_id,
            reason="Use the authorized test directory.",
        )],
        active_contract_id=corrected.contract_id,
    )


def test_history_round_trip_preserves_original_and_corrected_evidence():
    corrected_history = _corrected_history()
    loaded = ArtifactTargetHandoffRecord.model_validate_json(corrected_history.model_dump_json())
    assert loaded == corrected_history
    assert loaded.contracts[0].targets[0].project_relative_path == "src/test_greeting.py"
    assert loaded.contracts[1].targets[0].project_relative_path == "tests/test_greeting.py"
    assert [result.outcome for result in loaded.reconciliations] == ["clarification-required", "ready"]
    assert loaded.corrections[0].corrected_contract_id == loaded.active_contract_id


@pytest.mark.parametrize("member,field,value", [
    pytest.param(None, "active_contract_id", None, id="aggregate"),
    pytest.param("contracts", "project_source_revision", "source-r2", id="contract"),
    pytest.param("reconciliations", "outcome", "ready", id="reconciliation"),
    pytest.param("corrections", "reason", "Rewritten history", id="correction"),
])
def test_nested_history_is_immutable(member, field, value):
    corrected_history = _corrected_history()
    subject = corrected_history if member is None else getattr(corrected_history, member)[0]
    with pytest.raises(ValidationError, match="frozen"):
        setattr(subject, field, value)


@pytest.mark.parametrize("mutation,match", [
    pytest.param("duplicate-contract", "contract IDs must be unique", id="duplicate-contract"),
    pytest.param("missing-predecessor", "supersession chain", id="missing-predecessor"),
    pytest.param("superseded-active", "unsuperseded", id="superseded-active"),
    pytest.param("unknown-active", "unsuperseded", id="unknown-active"),
    pytest.param("unknown-result", "unknown contract", id="unknown-reconciliation-target"),
    pytest.param("duplicate-result", "reconciliation IDs", id="duplicate-reconciliation"),
    pytest.param("stale-ready", "matching source revision", id="stale-ready"),
    pytest.param("duplicate-action", "action IDs", id="duplicate-correction-action"),
    pytest.param("unknown-correction", "unknown corrected contract", id="unknown-correction"),
    pytest.param("wrong-predecessor", "contract it supersedes", id="wrong-correction-predecessor"),
    pytest.param("unknown-version", "schema_version", id="unknown-schema"),
])
def test_corrupt_history_fails_closed(mutation, match):
    corrected_history = _corrected_history()
    raw = corrected_history.model_dump(mode="json")
    if mutation == "duplicate-contract":
        raw["contracts"].append(raw["contracts"][-1])
    elif mutation == "missing-predecessor":
        raw["contracts"].pop(0)
    elif mutation == "superseded-active":
        raw["active_contract_id"] = raw["contracts"][0]["contract_id"]
    elif mutation == "unknown-active":
        raw["active_contract_id"] = "c" * 64
    elif mutation == "unknown-result":
        raw["reconciliations"][0]["contract_id"] = "c" * 64
    elif mutation == "duplicate-result":
        raw["reconciliations"].append(raw["reconciliations"][-1])
    elif mutation == "stale-ready":
        raw["reconciliations"][-1]["project_source_revision"] = "source-r2"
    elif mutation == "duplicate-action":
        raw["corrections"].append(raw["corrections"][0])
    elif mutation == "unknown-correction":
        raw["corrections"][0]["corrected_contract_id"] = "c" * 64
    elif mutation == "wrong-predecessor":
        raw["corrections"][0]["previous_contract_id"] = None
    elif mutation == "unknown-version":
        raw["schema_version"] = "9.0"
    with pytest.raises(ValidationError, match=match):
        ArtifactTargetHandoffRecord.model_validate(raw)


@pytest.mark.parametrize("updates,match", [
    pytest.param({"contract_id": None}, "requires a contract", id="ready-without-contract"),
    pytest.param({"reason_codes": ["unsafe-path"]}, "no failure reasons", id="ready-with-failure"),
    pytest.param({"outcome": "clarification-required"}, "at least one reason", id="unexplained-clarification"),
    pytest.param({"occurred_at": "2026-09-06T00:00:00"}, "timezone", id="naive-timestamp"),
])
def test_reconciliation_requires_consistent_disposition(updates, match):
    corrected_history = _corrected_history()
    raw = corrected_history.reconciliations[-1].model_dump()
    with pytest.raises(ValidationError, match=match):
        ArtifactTargetReconciliation.model_validate({**raw, **updates})


def test_no_contract_is_inferred_from_retained_history():
    corrected_history = _corrected_history()
    pending = ArtifactTargetHandoffRecord.model_validate({
        **corrected_history.model_dump(), "active_contract_id": None,
    })
    assert pending.active_contract_id is None
    assert pending.contracts == corrected_history.contracts
