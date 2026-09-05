"""Focused read-model tests for the BTN-42 desktop application boundary."""

from support.state import make_run_state

from datetime import datetime, timezone
from uuid import UUID

import pytest

from battalion.application import (
    InspectIntel,
    InspectProject,
    ProjectReadFailed,
    inspect_intel,
    inspect_project,
)
from battalion.identity import ProjectRunCatalog, RunCatalogEntry, load_project_identity
from battalion.intel import (
    AcceptedInstinct,
    AcceptanceProvenance,
    CandidateInstinct,
    CandidateRepository,
    InstinctApplicability,
    InstinctAudience,
    InstinctCreationProvenance,
    InstinctEvidenceReference,
    IntelRepository,
)
from battalion.state.models import RunState, RunStatus


def _state(project_id: UUID, run_id: str = "legacy-run") -> RunState:
    return make_run_state(
        run_id=run_id,
        project_id=str(project_id),
        ticket_id='BTN-42',
        status=RunStatus.DONE,
        phase='done',
        write_scope={},
        budget_limit=10,
    )


def _instinct(model_type, *, accepted: bool = False):
    evidence = InstinctEvidenceReference(
        run_id="run-1",
        node_execution_id="node-1",
        reference="state.json",
        description="Durable evidence",
    )
    payload = {
        "instinct_id": "INS-BTN-42",
        "recommendation": "Keep desktop queries read-only.",
        "evidence": [evidence],
        "audience": [InstinctAudience.DRIVER],
        "applicability": InstinctApplicability(description="Desktop clients"),
        "tags": ["desktop"],
        "creation_provenance": InstinctCreationProvenance(
            originating_run_id="run-1",
            originating_node_execution_ids=["node-1"],
            created_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
            created_by="recon",
        ),
    }
    if accepted:
        payload.update(
            lifecycle="accepted",
            acceptance_provenance=AcceptanceProvenance(
                accepted_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
                accepted_by="operator",
            ),
        )
    return model_type.model_validate(payload)


def test_project_query_represents_empty_and_legacy_history(tmp_path):
    identity = load_project_identity(tmp_path, create=True)

    empty = inspect_project(InspectProject(tmp_path))
    assert empty.identity == identity
    assert empty.runs == ()

    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir()
    state = _state(identity.project_id)
    (state_dir / "legacy-run.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )

    history = inspect_project(InspectProject(tmp_path))
    assert len(history.runs) == 1
    assert history.runs[0].catalog_entry.legacy_id is True
    assert history.runs[0].availability == "available"
    assert history.runs[0].inspection.state == state
    assert history.runs[0].inspection.workflow_admission is not None
    assert history.runs[0].inspection.workflow_admission.availability == "legacy"


def test_project_query_keeps_malformed_run_visible(tmp_path):
    identity = load_project_identity(tmp_path, create=True)
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir()
    (state_dir / "broken.json").write_text("not json", encoding="utf-8")
    catalog = ProjectRunCatalog(
        project_id=identity.project_id,
        runs=[RunCatalogEntry(
            run_id="broken",
            display_alias="BTN-42-broken",
            ticket_id="BTN-42",
            state_path=".battalion/state/broken.json",
            legacy_id=True,
        )],
    )
    (tmp_path / ".battalion" / "runs.json").write_text(
        catalog.model_dump_json(), encoding="utf-8"
    )

    result = inspect_project(InspectProject(tmp_path))
    assert result.runs[0].availability == "malformed"
    assert result.runs[0].inspection is None
    assert result.runs[0].limitation


def test_project_query_keeps_uncataloged_malformed_legacy_state_visible(tmp_path):
    load_project_identity(tmp_path, create=True)
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir()
    (state_dir / "orphaned-broken.json").write_text("not json", encoding="utf-8")

    result = inspect_project(InspectProject(tmp_path))

    assert len(result.runs) == 1
    assert result.runs[0].catalog_entry.run_id == "orphaned-broken"
    assert result.runs[0].catalog_entry.ticket_id == "Unknown ticket"
    assert result.runs[0].availability == "malformed"
    assert result.runs[0].limitation


def test_project_query_reports_inaccessible_project(tmp_path):
    with pytest.raises(ProjectReadFailed) as raised:
        inspect_project(InspectProject(tmp_path))
    assert raised.value.project_root == tmp_path.resolve()


def test_intel_query_discovers_candidates_and_accepted_knowledge(tmp_path):
    candidate = _instinct(CandidateInstinct)
    accepted = _instinct(AcceptedInstinct, accepted=True)
    CandidateRepository(
        tmp_path / ".battalion" / "recon" / "candidates"
    ).store(candidate)
    IntelRepository(tmp_path / ".battalion" / "intel").store(accepted)

    result = inspect_intel(InspectIntel(tmp_path))

    assert result.candidates == (candidate,)
    assert result.accepted == (accepted,)
