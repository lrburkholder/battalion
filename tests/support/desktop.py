"""Shared desktop inspection scenarios and Qt fixture."""


from __future__ import annotations


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from PySide6.QtWidgets import QApplication
from battalion.application import ProjectInspection, ProjectRunInspection, RunInspection
from battalion.intel.models import CandidateInstinct
from battalion.desktop.controller import DesktopController
from battalion.identity import ProjectIdentity, RunCatalogEntry
from battalion.state.models import (
    ArtifactProvenance,
    CheckpointType,
    CodeProvenance,
    CostSource,
    EvidenceReference,
    ExecutionRecord,
    LLMCallCost,
    NodeExecution,
    OperatorSummary,
    PromptProvenance,
    ReviewResult,
    RunStatus,
    TestOutcome as ExecutionTestOutcome,
)
from battalion.workers import WorkerRecord
from support.state import make_run_state
from support.execution import make_node_execution


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


def _execution() -> NodeExecution:
    return make_node_execution(
        execution_id="node-driver-green-1",
        role="driver",
        phase="driver_green",
        model_identity="provider/driver-model",
        input_references=[EvidenceReference(
            kind="workspace",
            reference="battalion/application.py",
            sha256="1" * 64,
            hash_algorithm="sha256",
            inclusion_reason="approved implementation context",
            observed_bytes=100,
            hashed_bytes=100,
        )],
        output_reference="battalion/desktop/app.py",
        started_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 20, 10, 1, tzinfo=timezone.utc),
        outcome="succeeded",
        test_outcome=ExecutionTestOutcome(
            checkpoint=CheckpointType.GREEN_CHECK,
            passed=True,
            expected_to_pass=True,
            accepted=True,
        ),
        review_result=ReviewResult(
            checkpoint=CheckpointType.GREEN_CHECK,
            verdict="accepted",
        ),
        artifact_provenance=[ArtifactProvenance(
            path="battalion/desktop/app.py",
            sha256="2" * 64,
            originating_run_id="run-42",
            originating_node_execution_id="node-driver-green-1",
        )],
        llm_calls=[
            LLMCallCost(
                call_id="known-call",
                model="provider/driver-model",
                input_tokens=120,
                output_tokens=45,
                cost=Decimal("0.125"),
                cost_currency="USD",
                cost_source=CostSource.PROVIDER_REPORTED,
            ),
            LLMCallCost(
                call_id="unknown-call",
                model="provider/driver-model",
                input_tokens=20,
                output_tokens=5,
            ),
        ],
        operator_summary=OperatorSummary(
            what_i_did="Built the read-only console.",
            what_should_happen_next="Validate accessibility.",
            verification_performed=["Focused desktop tests passed."],
            artifact_paths=["battalion/desktop/app.py"],
            last_role="driver",
            last_node="driver_green",
            last_phase="driver_green",
        ),
        prompt_provenance=PromptProvenance(
            template_identity="driver/green",
            template_path="prompts/driver.md",
            contract_version="driver/v1",
            template_hash="3" * 64,
            battalion_revision="4" * 40,
            model_configuration_identity="5" * 64,
        ),
        code_provenance=CodeProvenance(
            repository_available=True,
            base_commit_object_id="6" * 40,
            object_id_algorithm="sha1",
            branch="codex/btn-42",
            detached=False,
            dirty_at_start=False,
            dirty_at_end=True,
            exact_workspace_reconstructable=False,
            reconstruction_limitation="dirty-workspace-patch-not-retained",
        ),
    )


def _run(
    status: RunStatus,
    *,
    run_id: str,
    legacy: bool = True,
    execution: NodeExecution | None = None,
) -> ProjectRunInspection:
    state = make_run_state(
        run_id=run_id,
        ticket_id="BTN-42",
        status=status,
        phase="driver_green" if status is RunStatus.IN_PROGRESS else "done",
        write_scope={},
        budget_limit=20,
        execution_record=ExecutionRecord(
            node_executions=[execution] if execution is not None else []
        ),
    )
    entry = RunCatalogEntry(
        run_id=run_id,
        display_alias=f"BTN-42-{run_id}",
        ticket_id="BTN-42",
        state_path=f".battalion/state/{run_id}.json",
        legacy_id=legacy,
    )
    return ProjectRunInspection(
        catalog_entry=entry,
        availability="available",
        inspection=RunInspection(
            run_id=run_id,
            run_alias=entry.display_alias,
            state_version=state.schema_version,
            state_path=Path(entry.state_path),
            state=state,
            costs={},
        ),
    )


def _project(*runs: ProjectRunInspection) -> ProjectInspection:
    return ProjectInspection(
        project_root=Path("project"),
        identity=ProjectIdentity(
            project_id=UUID("42000000-0000-4000-8000-000000000042"),
            created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        ),
        runs=tuple(runs),
    )


def _candidate() -> CandidateInstinct:
    return CandidateInstinct.model_validate({
        "schema_version": "1.0",
        "instinct_id": "INS-DESKTOP-ACTION",
        "lifecycle": "candidate",
        "recommendation": "Use canonical desktop action commands.",
        "evidence": [{
            "run_id": "run-42",
            "node_execution_id": "node-driver-green-1",
            "reference": "execution_record.node_executions[0]",
            "description": "The application boundary retained authority.",
        }],
        "audience": ["driver"],
        "applicability": {"description": "Desktop actions"},
        "tags": ["desktop"],
        "creation_provenance": {
            "originating_run_id": "run-42",
            "originating_node_execution_ids": ["node-driver-green-1"],
            "created_at": "2026-08-20T12:00:00Z",
            "created_by": "recon",
        },
    })


class StubController(DesktopController):
    def __init__(self, tmp_path: Path, worker: WorkerRecord | None = None) -> None:
        super().__init__(tmp_path)
        self.worker = worker
        self.refresh_count = 0
        self.resumes = []
        self.interventions = []
        self.reviews = []
        self.admissions = []

    def refresh(self) -> None:
        self.refresh_count += 1
        self.loading.emit()

    def worker_for(self, run_id: str) -> WorkerRecord | None:
        return self.worker

    def resolve_and_resume(self, run_id, resolution):
        self.resumes.append((run_id, resolution))

    def queue_intervention(self, run_id, kind, target, text):
        self.interventions.append((run_id, kind, target, text))

    def review_candidate(self, candidate_id, action, edits=None):
        self.reviews.append((candidate_id, action, edits))

    def submit_admission_decision(self, session, disposition, annotation=None):
        self.admissions.append((session, disposition, annotation))
