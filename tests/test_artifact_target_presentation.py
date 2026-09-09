from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID

from battalion.application import ArtifactTargetHandoffInspection
from battalion.cli import app
from battalion.artifact_target_presentation import (
    artifact_target_handoff_payload,
    render_artifact_target_handoff,
)
from battalion.artifact_targets import ArtifactTargetContract
from battalion.artifact_target_state import ArtifactTargetReconciliation
from battalion.state.persistence import save_state
from support.cli import make_paused_state
from typer.testing import CliRunner


def _contract() -> ArtifactTargetContract:
    return ArtifactTargetContract(
        project_id=UUID("bd4b6e64-25fd-408a-a747-9633a803f036"),
        work_item_revision="work-r1",
        specification_revision="spec-r1",
        project_source_revision="source-r1",
        workflow_admission_decision_id="admission:1",
        evidence_references=[{
            "evidence_id": "work:1",
            "source": "work-item",
            "source_revision": "work-r1",
        }],
        targets=[{
            "target_id": "greeting-test",
            "project_relative_path": "tests/test_greeting.py",
            "assignments": [{
                "owner_role": "driver",
                "workflow_phase": "driver-red",
                "intended_operation": "create",
            }],
        }],
    )


def test_projection_distinguishes_active_contract_and_reconciliation_reason():
    contract = _contract()
    inspection = ArtifactTargetHandoffInspection(
        run_id="run-1",
        state_path=Path("state.json"),
        availability="available",
        active_contract_id=contract.contract_id,
        contracts=(contract,),
        reconciliations=(ArtifactTargetReconciliation(
            reconciliation_id="reconciliation:1",
            contract_id=contract.contract_id,
            occurred_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
            outcome="clarification-required",
            reason_codes=["out-of-scope"],
            recipe_id="full-implementation-run",
            recipe_version="1.0",
            project_source_revision="source-r1",
            write_scope_digest="a" * 64,
            path_policy_digest="b" * 64,
        ),),
        corrections=(),
    )

    payload = artifact_target_handoff_payload(inspection)
    assert payload["active_contract_id"] == contract.contract_id
    assert payload["superseded_contract_ids"] == []
    assert payload["contracts"][0]["targets"][0]["project_relative_path"] == "tests/test_greeting.py"
    assert payload["reconciliations"][0]["reason_codes"] == ["out-of-scope"]
    rendered = render_artifact_target_handoff(inspection)
    assert "(active)" in rendered
    assert "clarification-required" in rendered
    assert "out-of-scope" in rendered


def test_legacy_projection_explicitly_reports_unavailable_evidence(tmp_path):
    inspection = ArtifactTargetHandoffInspection(
        run_id="legacy-1",
        state_path=tmp_path / "state.json",
        availability="legacy",
        active_contract_id=None,
        contracts=(),
        reconciliations=(),
        corrections=(),
        limitation="Run has no persisted artifact-target handoff evidence.",
    )

    payload = artifact_target_handoff_payload(inspection)
    assert payload["availability"] == "legacy"
    assert payload["active_contract_id"] is None
    assert "unavailable" not in payload["availability"]
    assert "no persisted artifact-target handoff evidence" in render_artifact_target_handoff(inspection)


def test_cli_target_handoff_without_action_is_read_only_json(tmp_path, monkeypatch):
    state = make_paused_state("legacy-inspection")
    state_dir = tmp_path / ".battalion" / "state"
    save_state(state, state_dir / f"{state.run_id}.json")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["target-handoff", state.run_id, "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["availability"] == "legacy"
    assert payload["active_contract_id"] is None
