"""BTN-42 production desktop operator-console acceptance tests."""

from __future__ import annotations

import os
import configparser
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4, uuid5

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QAction, QFontDatabase
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from battalion.application import (
    IntelInspection,
    ProjectInspection,
    ProjectRunInspection,
    RunInspection,
)
from battalion.intel.candidates import CandidateDisposition, CandidateInboxEntry
from battalion.intel.models import CandidateInstinct
from battalion.intel.review import ReviewAction
from battalion.desktop.app import BattalionWindow
from battalion.desktop.demo import showcase_snapshot
from battalion.desktop.controller import DesktopController
from battalion.desktop.presentation import render_execution
from battalion.desktop.theme import (
    APPLICATION_ICON,
    BRAND_ICON,
    FONT_FILES,
    FONT_ROOT,
    MONO_FONT_FAMILY,
    SANS_FONT_FAMILY,
)
from battalion.identity import ProjectIdentity, RunCatalogEntry, load_project_identity
from battalion.observation import (
    ObservationCategory,
    ObservationCursor,
    ObservationEvent,
    ObservationKind,
)
from battalion.state.models import (
    ArtifactProvenance,
    Budget,
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
    RoleContractViolationEvidence,
    RunState,
    RunStatus,
    TestExecutionClassification as Classification,
    TestExecutionEvidence as ExecutionTestEvidence,
    TestOutcome as ExecutionTestOutcome,
)
from battalion.workers import WorkerRecord, WorkerStatus, _write_record


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_project_menu_reserves_space_for_refresh_shortcut(qt_app, tmp_path):
    window = BattalionWindow(tmp_path, autoload=False)
    menu = window.project_menu
    action = window.refresh_action

    menu.ensurePolished()
    required_width = (
        menu.fontMetrics().horizontalAdvance(action.text())
        + menu.fontMetrics().horizontalAdvance(action.shortcut().toString())
        + 48
    )

    assert menu.sizeHint().width() >= required_width
    window.close()


def test_data_handling_help_opens_only_the_disclosure_on_request(qt_app, tmp_path, monkeypatch):
    from battalion.disclosure import DATA_HANDLING_URL

    opened = []
    monkeypatch.setattr("battalion.desktop.app.QDesktopServices.openUrl", opened.append)
    window = BattalionWindow(tmp_path, autoload=False)
    try:
        assert opened == []
        assert window.data_handling_action in window.help_menu.actions()
        window.data_handling_action.trigger()
        assert [url.toString() for url in opened] == [DATA_HANDLING_URL]
        assert not (tmp_path / ".battalion").exists()
    finally:
        window.close()


def _execution() -> NodeExecution:
    return NodeExecution(
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
    state = RunState(
        schema_version="1.0",
        run_id=run_id,
        ticket_id="BTN-42",
        status=status,
        phase="driver_green" if status is RunStatus.IN_PROGRESS else "done",
        retry_bound=2,
        budget=Budget(limit=20),
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


def test_execution_inspector_exposes_provenance_verification_and_cost_semantics():
    rendered = render_execution(_execution())

    assert "Role: driver" in rendered
    assert "Phase: driver_green" in rendered
    assert "Model: provider/driver-model" in rendered
    assert "Contract version: driver/v1" in rendered
    assert f"Template hash (sha256): {'3' * 64}" in rendered
    assert f"Battalion revision: {'4' * 40}" in rendered
    assert f"Base revision (sha1): {'6' * 40}" in rendered
    assert "Dirty at end: yes" in rendered
    assert "Exact workspace reconstructable: no" in rendered
    assert "approved implementation context" in rendered
    assert "battalion/desktop/app.py · sha256" in rendered
    assert "Tests: green-check" in rendered
    assert "Review: green-check · accepted" in rendered
    assert "input=120 · output=45 · cost=0.125 USD · source=provider-reported" in rendered
    assert "unknown-call" in rendered and "cost=unknown · source=unknown" in rendered


def test_execution_inspector_exposes_reviewer_process_evidence():
    execution = _execution().model_copy(update={
        "role": "reviewer",
        "phase": "reviewer_green",
        "review_result": ReviewResult(
            checkpoint=CheckpointType.GREEN_CHECK,
            verdict="unavailable",
        ),
        "test_execution": ExecutionTestEvidence(
            command=["python", "-m", "pytest", "-q"],
            working_directory="clean-project-root",
            classification=Classification.TIMED_OUT,
            stdout="bounded evidence",
            stderr="harness unavailable",
            stdout_observed_bytes=16,
            stderr_observed_bytes=19,
            duration_ms=5000,
            timeout_seconds=5,
            timed_out=True,
            cleanup_attempted=True,
            cleanup_succeeded=True,
        ),
    })

    rendered = render_execution(execution)

    assert "Review: green-check · unavailable" in rendered
    assert "Pytest classification: timeout" in rendered
    assert "Working directory: clean-project-root" in rendered
    assert "Duration: 5000 ms; timeout: 5 s" in rendered
    assert "Cleanup attempted: yes; succeeded: yes" in rendered
    assert "bounded evidence" in rendered
    assert "harness unavailable" in rendered


def test_execution_inspector_exposes_nonblocking_contract_correction_evidence():
    execution = _execution().model_copy(update={
        "outcome": "rejected",
        "attempt_disposition": "corrected",
        "role_contract_violation": RoleContractViolationEvidence(
            reason_code="driver-mode-artifact",
            detail="GREEN mode must not produce test files",
            offending_paths=["tests/test_widget.py"],
            attempt_number=1,
            resulting_disposition="retry",
        ),
    })

    rendered = render_execution(execution)

    assert "Attempt disposition: corrected" in rendered
    assert "ROLE-CONTRACT CORRECTION" in rendered
    assert "Mutation applied: no" in rendered
    assert "Disposition: retry" in rendered
    assert "tests/test_widget.py" in rendered


def test_window_presents_work_history_and_human_action_surfaces(qt_app, tmp_path):
    controller = StubController(tmp_path)
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)
    active = _run(RunStatus.IN_PROGRESS, run_id="active", execution=_execution())
    done = _run(RunStatus.DONE, run_id="done")

    window.render_snapshot(_project(active, done), IntelInspection((), ()))

    assert [window.navigation.item(index).text() for index in range(3)] == [
        "Work", "History", "Intel"
    ]
    assert window.work_tree.topLevelItem(0).childCount() == 1
    assert window.history_tree.topLevelItem(0).childCount() == 1
    work_item = window.work_tree.topLevelItem(0).child(0)
    window.work_tree.setCurrentItem(work_item)
    assert window.work_executions.topLevelItemCount() == 1
    window.work_executions.setCurrentItem(window.work_executions.topLevelItem(0))
    assert "PROMPT PROVENANCE" in window.work_inspector.toPlainText()
    assert window.intel_tree.topLevelItem(1).child(0).text(0) == "No Intel or Recon evidence"

    forbidden = {"resume", "promote", "reject", "accept", "cancel", "start run"}
    action_text = {action.text().lower() for action in window.findChildren(QAction)}
    assert not forbidden.intersection(action_text)
    button_text = {button.text() for button in window.findChildren(QPushButton)}
    assert button_text == {
        "Resolve and resume", "Queue for next attempt", "Promote",
        "Edit and promote", "Reject",
    }
    window.close()


def test_desktop_loads_bundled_brand_fonts_and_icon(qt_app, tmp_path):
    window = BattalionWindow(tmp_path, autoload=False)

    assert {SANS_FONT_FAMILY, MONO_FONT_FAMILY} <= set(QFontDatabase.families())
    assert all((FONT_ROOT / filename).is_file() for filename in FONT_FILES)
    assert APPLICATION_ICON.is_file()
    assert BRAND_ICON.is_file()
    assert not window.windowIcon().isNull()
    assert window.work_inspector.font().family() == MONO_FONT_FAMILY
    assert "#1a1b1e" in window.styleSheet()
    assert "#5b8dd6" in window.styleSheet()
    window.close()


def test_work_actions_are_exact_targeted_and_resume_only_paused_runs(qt_app, tmp_path):
    controller = StubController(tmp_path)
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)
    paused = _run(RunStatus.AWAITING_HUMAN, run_id="paused")
    window.render_snapshot(_project(paused), IntelInspection((), ()))
    window.work_tree.setCurrentItem(window.work_tree.topLevelItem(0).child(0))

    assert window.resume_button.isEnabled()
    assert [window.intervention_target.itemText(index) for index in range(3)] == [
        "Driver RED", "Driver GREEN", "Refactorer"
    ]
    window.intervention_kind.setCurrentIndex(1)
    assert [window.intervention_target.itemText(index) for index in range(1)] == [
        "Architect"
    ]
    window.resolution_edit.setText("Approved after review")
    window.resume_button.click()
    assert controller.resumes == [
        ("paused", "Approved after review")
    ]
    window.intervention_text.setText("Use ADR-0023")
    window.queue_button.click()
    assert controller.interventions[0][0] == "paused"
    assert controller.interventions[0][2] == "architect"
    window.close()


def test_pending_candidate_actions_use_canonical_review_intents(qt_app, tmp_path):
    controller = StubController(tmp_path)
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)
    candidate = _candidate()
    entry = CandidateInboxEntry(candidate, CandidateDisposition.PENDING, None)
    window.render_snapshot(
        _project(), IntelInspection((), (candidate,), (entry,))
    )
    item = window.intel_tree.topLevelItem(1).child(0)
    window.intel_tree.setCurrentItem(item)

    assert item.text(1) == "pending"
    assert window.accept_candidate_button.isEnabled()
    window.accept_candidate_button.click()

    assert controller.reviews == [
        (candidate.instinct_id, ReviewAction.ACCEPT, None)
    ]
    window.close()


def test_explicit_loading_empty_malformed_crashed_and_inaccessible_states(
    qt_app, tmp_path
):
    durable_state = tmp_path / "crashed.json"
    durable_state.write_text("{}", encoding="utf-8")
    worker = WorkerRecord(
        run_id="active",
        state_version="1.0",
        worker_id="worker-42",
        operation="start",
        status=WorkerStatus.CRASHED,
        pid=4242,
        started_at="2026-08-20T10:00:00+00:00",
        updated_at="2026-08-20T10:01:00+00:00",
        state_path=str(durable_state),
        error="Worker exited abnormally",
    )
    controller = StubController(tmp_path, worker)
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)

    window.show_loading()
    assert "Loading authoritative" in window.view_state.text()
    window.render_snapshot(_project(), IntelInspection((), ()))
    assert window.view_state.property("state") == "empty"

    malformed = ProjectRunInspection(
        catalog_entry=RunCatalogEntry(
            run_id="broken",
            display_alias="BTN-42-broken",
            ticket_id="BTN-42",
            state_path=".battalion/state/broken.json",
            legacy_id=True,
        ),
        availability="malformed",
        limitation="invalid JSON",
    )
    active = _run(RunStatus.IN_PROGRESS, run_id="active")
    window.render_snapshot(_project(active, malformed), IntelInspection((), ()))
    active_item = window.work_tree.topLevelItem(0).child(0)
    window.work_tree.setCurrentItem(active_item)
    assert "Worker: crashed · recoverable from durable state" in window.work_inspector.toPlainText()
    malformed_item = window.history_tree.topLevelItem(0).child(0)
    window.history_tree.setCurrentItem(malformed_item)
    assert "Availability: malformed" in window.history_inspector.toPlainText()
    assert "Limitation: invalid JSON" in window.history_inspector.toPlainText()

    window.show_error("No project identity")
    assert window.view_state.property("state") == "error"
    assert "Inaccessible project" in window.view_state.text()
    window.close()


def test_every_primary_surface_has_an_accessible_name(qt_app, tmp_path):
    window = BattalionWindow(
        tmp_path, controller=StubController(tmp_path), autoload=False
    )
    widgets = (
        window.navigation,
        window.pages,
        window.work_tree,
        window.work_executions,
        window.work_inspector,
        window.history_tree,
        window.history_executions,
        window.history_inspector,
        window.intel_tree,
        window.intel_inspector,
        window.view_state,
        window.live_state,
    )
    assert all(widget.accessibleName() for widget in widgets)
    interactive = (window.navigation, *widgets[2:10])
    assert all(
        widget.focusPolicy() != Qt.FocusPolicy.NoFocus for widget in interactive
    )
    window.close()


def test_keyboard_navigation_reaches_every_destination(qt_app, tmp_path):
    window = BattalionWindow(
        tmp_path, controller=StubController(tmp_path), autoload=False
    )
    window.show()
    window.navigation.setFocus()
    window.navigation.setCurrentRow(0)

    QTest.keyClick(window.navigation, Qt.Key.Key_Down)
    assert window.navigation.currentItem().text() == "History"
    assert window.pages.currentIndex() == 1
    QTest.keyClick(window.navigation, Qt.Key.Key_Down)
    assert window.navigation.currentItem().text() == "Intel"
    assert window.pages.currentIndex() == 2

    window.close()


class ImmediateThreadPool:
    def start(self, callback):
        callback()


class RecoveringSource:
    def __init__(self, event: ObservationEvent) -> None:
        self.event = event

    def barrier(self, run_id: str, stream_id: UUID) -> ObservationCursor:
        return ObservationCursor(run_id=run_id, stream_id=stream_id, sequence=1)

    def after(self, cursor: ObservationCursor) -> tuple[ObservationEvent, ...]:
        assert cursor.sequence == 1
        return (self.event,)


def test_client_restart_recovers_durable_state_before_new_live_events(qt_app, tmp_path):
    identity = load_project_identity(tmp_path, create=True)
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir()
    state = RunState(
        schema_version="1.0",
        run_id="active",
        project_id=str(identity.project_id),
        ticket_id="BTN-42",
        status=RunStatus.IN_PROGRESS,
        phase="driver_green",
        retry_bound=2,
        budget=Budget(limit=10),
    )
    (state_dir / "active.json").write_text(state.model_dump_json(), encoding="utf-8")
    stream_id = uuid4()
    event = ObservationEvent(
        event_id=uuid5(stream_id, "2"),
        run_id="active",
        stream_id=stream_id,
        sequence=2,
        occurred_at=datetime.now(timezone.utc),
        category=ObservationCategory.DURABLE,
        kind=ObservationKind.STATE_CHECKPOINT,
        payload={"state_version": "1.0", "status": "in-progress", "phase": "reviewer"},
    )
    controller = DesktopController(tmp_path, thread_pool=ImmediateThreadPool())
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)
    order = []
    controller.durable_recovered.connect(lambda inspection: order.append(("durable", inspection.state.phase)))
    controller.live_observation.connect(lambda observed: order.append(("live", observed.sequence)))

    controller.recover_live("active", stream_id, RecoveringSource(event))

    assert order == [("durable", "driver_green"), ("live", 2)]
    assert "sequence 2 · state-checkpoint" in window.live_state.text()
    assert window.view_state.property("state") == "ready"
    window.close()


def test_desktop_modules_have_no_graph_or_persistence_authority():
    root = Path(__file__).resolve().parents[1] / "battalion" / "desktop"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    assert "battalion.graph" not in source
    assert "battalion.state.persistence" not in source
    assert "resume_run(" not in source
    assert "start_run(" not in source
    assert ".write_text(" not in source
    assert ".open(" not in source


def test_desktop_reconnect_represents_a_real_worker_process_exit(tmp_path):
    state_dir = tmp_path / ".battalion" / "state"
    worker_dir = tmp_path / ".battalion" / "workers"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "real-crash.json"
    state_path.write_text(
        RunState(
            schema_version="1.0",
            run_id="real-crash",
            ticket_id="BTN-42",
            status=RunStatus.IN_PROGRESS,
            phase="driver_green",
            retry_bound=2,
            budget=Budget(limit=10),
        ).model_dump_json(),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_record(
            worker_dir / "real-crash.json",
            WorkerRecord(
                run_id="real-crash",
                state_version="1.0",
                worker_id="real-process",
                operation="start",
                status=WorkerStatus.RUNNING,
                pid=process.pid,
                started_at="2026-08-20T10:00:00+00:00",
                updated_at="2026-08-20T10:00:00+00:00",
                state_path=str(state_path),
            ),
        )
        process.terminate()
        process.wait(timeout=10)

        recovered = DesktopController(tmp_path).worker_for("real-crash")

        assert recovered.status is WorkerStatus.CRASHED
        assert recovered.recoverable is True
        assert recovered.error == "Worker exited abnormally"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_desktop_packaging_contract_is_reproducible():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deployment = configparser.ConfigParser()
    deployment.read(root / "deployment" / "desktop" / "pysidedeploy.ini")

    assert project["project"]["optional-dependencies"]["desktop"] == [
        "PySide6==6.10.1",
        "Nuitka==4.1.3",
    ]
    assert "langgraph>=1.2,<2.0" in project["project"]["dependencies"]
    assert project["project"]["scripts"]["battalion-desktop"] == (
        "battalion.desktop.app:main"
    )
    assert deployment["app"]["project_dir"] == "."
    assert deployment["app"]["input_file"] == "battalion/desktop/__main__.py"
    assert deployment["nuitka"]["mode"] == "standalone"
    assert "--nofollow-import-to=battalion.graph" in deployment["nuitka"]["extra_args"]
    assert "--nofollow-import-to=litellm" in deployment["nuitka"]["extra_args"]
    assert "--nofollow-import-to=langgraph" in deployment["nuitka"]["extra_args"]
    assert "--noinclude-pytest-mode=nofollow" in deployment["nuitka"]["extra_args"]
    assert (
        "--include-data-dir=battalion/desktop/assets=battalion/desktop/assets"
        in deployment["nuitka"]["extra_args"]
    )
    assert "--report=dist/desktop/desktop-compilation-report.xml" in (
        deployment["nuitka"]["extra_args"]
    )
    assert deployment["python"]["python_path"] == ""
    assert deployment["app"]["icon"] == ""
    assert project["tool"]["setuptools"]["package-data"]["battalion.desktop"] == [
        "assets/*",
        "assets/fonts/*",
    ]
    assert set(deployment["qt"]["modules"].split(",")) == {"Core", "Gui", "Widgets"}
    assert "accessiblebridge" in deployment["qt"]["plugins"]


def test_desktop_entrypoint_launches_from_an_unrelated_working_directory(tmp_path):
    load_project_identity(tmp_path, create=True)
    screenshot = tmp_path / "desktop.png"
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "battalion.desktop",
            "--project",
            str(tmp_path),
            "--screenshot",
            str(screenshot),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_showcase_snapshot_selects_shipped_work_history_and_intel_views(qt_app, tmp_path):
    window = BattalionWindow(tmp_path, autoload=False)
    project, intel = showcase_snapshot()
    window.render_snapshot(project, intel)

    window.select_showcase_view("work")
    assert window.navigation.currentItem().text() == "Work"
    assert window.selected_run.inspection.state.status is RunStatus.AWAITING_HUMAN
    assert window.resume_button.isEnabled()

    window.select_showcase_view("history")
    assert window.navigation.currentItem().text() == "History"
    assert "Role: reviewer" in window.history_inspector.toPlainText()

    window.select_showcase_view("intel")
    assert window.navigation.currentItem().text() == "Intel"
    assert window.selected_candidate.instinct_id == "INS-REVIEW-EVIDENCE"
    assert window.accept_candidate_button.isEnabled()
    window.close()


def test_importing_battalion_does_not_initialize_graph_authority(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import battalion; "
                "assert 'battalion.graph' not in sys.modules; "
                "from battalion import RunState; "
                "assert RunState.__name__ == 'RunState'; "
                "assert 'battalion.graph' not in sys.modules"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
