"""CLI workflow admission and explicit human authorization."""


import json
from pathlib import Path
from typer.testing import CliRunner
from battalion.cli import app
from battalion.workflow_admission import CompactAdmissionEvidence, HardRiskFlag


runner = CliRunner()


def _admission_evidence_file(
    tmp_path: Path,
    *,
    compact: bool = True,
    hard_risk: str | None = None,
) -> Path:
    references = [{
        "evidence_id": "work-item:BTN-144",
        "source": "work-item",
        "source_revision": "BTN-144@1",
        "condition": "present",
        "authoritative": True,
        "hard_risk_flags": [hard_risk] if hard_risk else [],
    }]
    if compact:
        references.extend({
            "evidence_id": f"evidence:{fact.value}",
            "source": "repository",
            "source_revision": "repository@1",
            "condition": "present",
            "authoritative": True,
            "establishes": [fact.value],
        } for fact in CompactAdmissionEvidence)
    path = tmp_path / "admission-evidence.json"
    path.write_text(json.dumps({
        "work_item_revision": "BTN-144@1",
        "evidence_references": references,
    }), encoding="utf-8")
    return path


def test_admit_json_inspects_without_silently_authorizing(tmp_path, monkeypatch) -> None:
    evidence = _admission_evidence_file(tmp_path)

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, [
            "admit", "BTN-144", "--spec", "Present workflow admission.",
            "--evidence", str(evidence), "--json",
        ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["inspection"]["assessment"]["outcome"] == "compact-admissible"
    assert payload["inspection"]["available_actions"] == [
        "full", "compact", "clarification", "cancelled",
    ]
    assert payload["decision"] is None
    assert payload["run"] is None
    assert not (tmp_path / ".battalion").exists()


def test_admit_can_choose_full_when_compact_is_available(tmp_path, monkeypatch) -> None:
    evidence = _admission_evidence_file(tmp_path)

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, [
            "admit", "BTN-144", "--spec", "Present workflow admission.",
            "--evidence", str(evidence), "--decision", "full", "--json",
        ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"]["disposition"] == "full"
    assert payload["decision"]["selected_recipe_id"] == "full-implementation-run"
    assert payload["run"]["run_id"]
    assert list((tmp_path / ".battalion" / "state").glob("*.json"))


def test_admit_full_required_never_offers_or_accepts_compact(
    tmp_path, monkeypatch
) -> None:
    evidence = _admission_evidence_file(
        tmp_path,
        hard_risk=HardRiskFlag.AUTHORIZATION_SECRETS_PRIVACY_SECURITY.value,
    )

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        inspected = runner.invoke(app, [
            "admit", "BTN-144", "--spec", "Present workflow admission.",
            "--evidence", str(evidence), "--json",
        ])
        rejected = runner.invoke(app, [
            "admit", "BTN-144", "--spec", "Present workflow admission.",
            "--evidence", str(evidence), "--decision", "compact", "--json",
        ])

    payload = json.loads(inspected.output)
    assert payload["inspection"]["assessment"]["outcome"] == "full-required"
    assert "compact" not in payload["inspection"]["available_actions"]
    assert rejected.exit_code == 1
    assert "requires the full workflow" in rejected.output


def test_admit_json_reports_authorization_denial_explicitly(tmp_path, monkeypatch) -> None:
    evidence = _admission_evidence_file(tmp_path)

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, [
            "admit", "BTN-144", "--spec", "Present workflow admission.",
            "--evidence", str(evidence), "--decision", "full", "--actor-id",
            "00000000-0000-4000-8000-000000000144", "--json",
        ])

    assert result.exit_code == 1
    error = json.loads(result.output)
    assert "Unknown Actor" in error["error"]["message"]
    assert not (tmp_path / ".battalion" / "state").exists()
