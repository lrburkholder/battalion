"""Desktop window actions, accessibility, and operator surfaces."""


from __future__ import annotations


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


from PySide6.QtGui import QAction, QFontDatabase
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from battalion.application import IntelInspection, ProjectRunInspection
from battalion.intel.candidates import CandidateDisposition, CandidateInboxEntry
from battalion.intel.review import ReviewAction
from battalion.desktop.app import BattalionWindow
from battalion.desktop.demo import showcase_snapshot
from battalion.desktop.theme import (
    APPLICATION_ICON,
    BRAND_ICON,
    FONT_FILES,
    FONT_ROOT,
    MONO_FONT_FAMILY,
    SANS_FONT_FAMILY,
)
from battalion.identity import RunCatalogEntry
from battalion.state.models import RunStatus
from battalion.workers import WorkerRecord, WorkerStatus
from support.desktop import (
    StubController,
    _candidate,
    _execution,
    _project,
    _run,
    qt_app,
)


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


def test_window_presents_work_history_and_human_action_surfaces(qt_app, tmp_path):
    controller = StubController(tmp_path)
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)
    active = _run(RunStatus.IN_PROGRESS, run_id="active", execution=_execution())
    done = _run(RunStatus.DONE, run_id="done")

    window.render_snapshot(_project(active, done), IntelInspection((), ()))

    assert [window.navigation.item(index).text() for index in range(4)] == [
        "Work", "Admission", "History", "Intel"
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
        "Edit and promote", "Reject", "Inspect admission", "Use compact",
        "Use full", "Clarify", "Cancel",
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
        window.admission_ticket,
        window.admission_spec,
        window.admission_evidence,
        window.admission_tactician,
        window.admission_inspector,
        window.admission_annotation,
        window.load_admission_button,
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
    assert window.navigation.currentItem().text() == "Admission"
    assert window.pages.currentIndex() == 1
    QTest.keyClick(window.navigation, Qt.Key.Key_Down)
    assert window.navigation.currentItem().text() == "History"
    assert window.pages.currentIndex() == 2
    QTest.keyClick(window.navigation, Qt.Key.Key_Down)
    assert window.navigation.currentItem().text() == "Intel"
    assert window.pages.currentIndex() == 3

    window.close()


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
