"""Production PySide6 read-only operator console for Battalion."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from battalion.application import IntelInspection, ProjectInspection, ProjectRunInspection
from battalion.desktop.controller import DesktopController
from battalion.desktop.presentation import (
    intel_empty,
    partition_runs,
    render_execution,
    render_intel_item,
    render_run,
    run_label,
)
from battalion.desktop.theme import (
    BRAND_ICON,
    MONO_FONT_FAMILY,
    STYLESHEET,
    application_icon,
    load_bundled_fonts,
)
from battalion.observation import ObservationEvent, ObservationKind
from battalion.intel.models import CandidateInstinct
from battalion.intel.review import ReviewAction
from battalion.state.models import InterventionKind, InterventionTarget, RunStatus


RUN_DATA = Qt.ItemDataRole.UserRole
EXECUTION_DATA = Qt.ItemDataRole.UserRole + 1
INTEL_DATA = Qt.ItemDataRole.UserRole + 2


class BattalionWindow(QMainWindow):
    """Read-only Qt Widgets shell over application-boundary projections."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        controller: DesktopController | None = None,
        autoload: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller or DesktopController(project_root)
        load_bundled_fonts()
        self.setWindowTitle("Battalion")
        self.setWindowIcon(application_icon())
        self.setMinimumSize(900, 600)
        self.resize(1380, 860)
        self.setCentralWidget(self._build_content())
        self._build_menu()
        self.setStyleSheet(STYLESHEET)
        self._connect_controller()
        if autoload:
            self.controller.refresh()

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(8, 5, 8, 6)
        top_bar_layout.setSpacing(8)
        brand_mark = QLabel()
        brand_mark.setAccessibleName("Battalion mark")
        brand_mark.setPixmap(
            QPixmap(str(BRAND_ICON)).scaled(
                22,
                22,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        title = QLabel("battalion")
        title.setObjectName("title")
        title.setAccessibleName("Battalion operator console")
        separator = QLabel("│")
        separator.setObjectName("navSeparator")
        project_identity = QLabel(f"project  {self.controller.project_root.name}")
        project_identity.setObjectName("projectIdentity")
        project_identity.setAccessibleName("Current project")
        top_bar_layout.addWidget(brand_mark)
        top_bar_layout.addWidget(title)
        top_bar_layout.addWidget(separator)
        top_bar_layout.addWidget(project_identity)
        top_bar_layout.addStretch(1)
        self.view_state = QLabel("Loading project…")
        self.view_state.setObjectName("viewState")
        self.view_state.setAccessibleName("Project loading and error status")
        self.live_state = QLabel("Live connection: durable snapshot")
        self.live_state.setObjectName("liveState")
        self.live_state.setAccessibleName("Live run observation status")
        layout.addWidget(top_bar)
        layout.addWidget(self.view_state)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.navigation = QListWidget()
        self.navigation.setObjectName("primaryNavigation")
        self.navigation.setAccessibleName("Primary navigation")
        self.navigation.addItems(("Work", "History", "Intel"))
        self.navigation.setCurrentRow(0)
        self.navigation.setMaximumWidth(160)
        self.pages = QStackedWidget()
        self.pages.setAccessibleName("Operator destination")

        work, self.work_tree, self.work_executions, self.work_inspector = self._run_page(
            "Active and actionable work", "work"
        )
        work.layout().addWidget(self._work_actions())
        history, self.history_tree, self.history_executions, self.history_inspector = self._run_page(
            "Completed and earlier run history", "history"
        )
        intel = self._intel_page()
        self.pages.addWidget(work)
        self.pages.addWidget(history)
        self.pages.addWidget(intel)
        body.addWidget(self.navigation)
        body.addWidget(self.pages)
        body.setSizes((150, 1150))
        layout.addWidget(body, 1)
        layout.addWidget(self.live_state)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        return root

    def _run_page(
        self, heading_text: str, prefix: str
    ) -> tuple[QWidget, QTreeWidget, QTreeWidget, QPlainTextEdit]:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(heading_text)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        runs = QTreeWidget()
        runs.setObjectName(f"{prefix}Runs")
        runs.setAccessibleName(f"{heading_text} by ticket")
        runs.setHeaderLabels(("Ticket / run", "Status", "Phase"))
        runs.setRootIsDecorated(True)
        executions = QTreeWidget()
        executions.setObjectName(f"{prefix}Executions")
        executions.setAccessibleName(f"{heading_text} node attempts")
        executions.setHeaderLabels(("Attempt", "Role", "Phase", "Outcome"))
        inspector = QPlainTextEdit()
        inspector.setObjectName(f"{prefix}Inspector")
        inspector.setAccessibleName(f"{heading_text} evidence inspector")
        inspector.setReadOnly(True)
        inspector.setFont(QFont(MONO_FONT_FAMILY, 10))
        inspector.setPlainText("Select a run or node attempt to inspect its evidence.")

        splitter.addWidget(self._panel(runs, "Runs"))
        splitter.addWidget(self._panel(executions, "Execution map"))
        splitter.addWidget(self._panel(inspector, "Inspector"))
        splitter.setSizes((370, 380, 550))
        layout.addWidget(splitter, 1)
        runs.currentItemChanged.connect(
            lambda current, _previous: self._select_run(current, executions, inspector)
        )
        executions.currentItemChanged.connect(
            lambda current, _previous: self._select_execution(current, inspector)
        )
        return page, runs, executions, inspector

    def _intel_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Intel library and Recon candidates")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.intel_tree = QTreeWidget()
        self.intel_tree.setObjectName("intelLibrary")
        self.intel_tree.setAccessibleName("Accepted Intel and persisted Recon candidates")
        self.intel_tree.setHeaderLabels(("Knowledge", "Lifecycle"))
        self.intel_inspector = QPlainTextEdit()
        self.intel_inspector.setObjectName("intelInspector")
        self.intel_inspector.setAccessibleName("Intel evidence inspector")
        self.intel_inspector.setReadOnly(True)
        self.intel_inspector.setFont(QFont(MONO_FONT_FAMILY, 10))
        self.intel_inspector.setPlainText(
            "Select an Instinct. Pending Recon candidates can be promoted or rejected."
        )
        splitter.addWidget(self._panel(self.intel_tree, "Library"))
        splitter.addWidget(self._panel(self.intel_inspector, "Inspector"))
        splitter.setSizes((450, 800))
        layout.addWidget(splitter, 1)
        layout.addWidget(self._intel_actions())
        self.intel_tree.currentItemChanged.connect(self._select_intel)
        return page

    def _work_actions(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("actionPanel")
        layout = QVBoxLayout(panel)
        heading = QLabel("Human actions")
        heading.setObjectName("panelTitle")
        layout.addWidget(heading)

        identity = QHBoxLayout()
        identity.addWidget(QLabel("Actor"))
        self.actor_edit = QLineEdit(getpass.getuser() or "operator")
        self.actor_edit.setAccessibleName("Human action actor")
        identity.addWidget(self.actor_edit)
        identity.addWidget(QLabel("Interrupt resolution"))
        self.resolution_edit = QLineEdit("Reviewed and authorized to resume")
        self.resolution_edit.setAccessibleName("Interrupt resolution")
        identity.addWidget(self.resolution_edit, 1)
        self.resume_button = QPushButton("Resolve and resume")
        self.resume_button.setAccessibleName("Resolve interrupt and resume run")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self._resume_selected_run)
        identity.addWidget(self.resume_button)
        layout.addLayout(identity)

        intervention = QHBoxLayout()
        self.intervention_kind = QComboBox()
        self.intervention_kind.setAccessibleName("Intervention intent")
        self.intervention_kind.addItem("Correction", InterventionKind.CORRECTION)
        self.intervention_kind.addItem(
            "Design decision", InterventionKind.DESIGN_DECISION
        )
        self.intervention_target = QComboBox()
        self.intervention_target.setAccessibleName("Intervention target")
        self.intervention_text = QLineEdit()
        self.intervention_text.setAccessibleName("Bounded intervention text")
        self.intervention_text.setPlaceholderText(
            "Additional context for the target's next attempt"
        )
        self.queue_button = QPushButton("Queue for next attempt")
        self.queue_button.setAccessibleName("Queue intervention for next attempt")
        self.queue_button.setEnabled(False)
        self.queue_button.clicked.connect(self._queue_selected_intervention)
        self.intervention_kind.currentIndexChanged.connect(
            self._refresh_intervention_targets
        )
        intervention.addWidget(self.intervention_kind)
        intervention.addWidget(self.intervention_target)
        intervention.addWidget(self.intervention_text, 1)
        intervention.addWidget(self.queue_button)
        layout.addLayout(intervention)
        self._refresh_intervention_targets()
        self.selected_run: ProjectRunInspection | None = None
        return panel

    def _intel_actions(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("actionPanel")
        layout = QHBoxLayout(panel)
        layout.addWidget(QLabel("Review actor"))
        self.intel_actor_edit = QLineEdit(getpass.getuser() or "operator")
        self.intel_actor_edit.setAccessibleName("Recon review actor")
        layout.addWidget(self.intel_actor_edit, 1)
        self.accept_candidate_button = QPushButton("Promote")
        self.accept_candidate_button.setAccessibleName("Promote selected Recon candidate")
        self.edit_candidate_button = QPushButton("Edit and promote")
        self.edit_candidate_button.setAccessibleName(
            "Edit recommendation and promote selected Recon candidate"
        )
        self.reject_candidate_button = QPushButton("Reject")
        self.reject_candidate_button.setAccessibleName("Reject selected Recon candidate")
        for button in (
            self.accept_candidate_button,
            self.edit_candidate_button,
            self.reject_candidate_button,
        ):
            button.setEnabled(False)
            layout.addWidget(button)
        self.accept_candidate_button.clicked.connect(
            lambda: self._review_selected_candidate(ReviewAction.ACCEPT)
        )
        self.edit_candidate_button.clicked.connect(self._edit_selected_candidate)
        self.reject_candidate_button.clicked.connect(
            lambda: self._review_selected_candidate(ReviewAction.REJECT)
        )
        self.selected_candidate: CandidateInstinct | None = None
        return panel

    @staticmethod
    def _panel(content: QWidget, heading_text: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        heading = QLabel(heading_text)
        heading.setObjectName("panelTitle")
        layout.addWidget(heading)
        layout.addWidget(content, 1)
        return panel

    def _build_menu(self) -> None:
        self.refresh_action = QAction("Refresh authoritative state", self)
        self.refresh_action.setShortcut("Ctrl+R")
        self.refresh_action.triggered.connect(self.controller.refresh)
        self.project_menu = self.menuBar().addMenu("Project")
        self.project_menu.addAction(self.refresh_action)

    def _connect_controller(self) -> None:
        self.controller.loading.connect(self.show_loading)
        self.controller.snapshot_ready.connect(self.render_snapshot)
        self.controller.load_failed.connect(self.show_error)
        self.controller.durable_recovered.connect(self.render_recovered_run)
        self.controller.live_observation.connect(self.apply_observation)
        self.controller.action_completed.connect(self._action_completed)
        self.controller.action_failed.connect(self._action_failed)

    def show_loading(self) -> None:
        self.view_state.setText("Loading authoritative project state…")
        self.view_state.setProperty("state", "loading")

    def show_error(self, message: str) -> None:
        self.view_state.setText(f"Inaccessible project: {message}")
        self.view_state.setProperty("state", "error")
        self.work_tree.clear()
        self.history_tree.clear()
        self.intel_tree.clear()

    def _action_completed(self, message: str) -> None:
        self.live_state.setText(f"Human action recorded · {message}")

    def _action_failed(self, message: str) -> None:
        self.live_state.setText(f"Human action rejected · {message}")

    def render_snapshot(
        self, project: ProjectInspection, intel: IntelInspection
    ) -> None:
        work, history = partition_runs(project.runs)
        self._populate_runs(self.work_tree, work, "No active or actionable runs")
        self._populate_runs(self.history_tree, history, "No run history")
        self._populate_intel(intel)
        if not project.runs and intel_empty(intel):
            message = "Empty project: no runs, accepted Intel, or Recon candidates"
            state = "empty"
        else:
            message = (
                f"Ready · {len(work)} work run(s) · {len(history)} history run(s) · "
                f"{len(intel.accepted)} accepted Instinct(s) · "
                f"{len(intel.candidates)} Recon candidate(s)"
            )
            state = "ready"
        self.view_state.setText(message)
        self.view_state.setProperty("state", state)

    def select_showcase_view(self, view: str) -> None:
        """Select stable production widgets for a published showcase capture."""

        destinations = {"work": 0, "history": 1, "intel": 2}
        if view not in destinations:
            raise ValueError(f"Unknown showcase view: {view}")
        self.navigation.setCurrentRow(destinations[view])
        if view == "intel":
            candidates = self.intel_tree.topLevelItem(1)
            if candidates is not None and candidates.childCount():
                self.intel_tree.setCurrentItem(candidates.child(0))
            return
        runs = self.work_tree if view == "work" else self.history_tree
        executions = self.work_executions if view == "work" else self.history_executions
        if runs.topLevelItemCount() and runs.topLevelItem(0).childCount():
            runs.setCurrentItem(runs.topLevelItem(0).child(0))
            if view == "history" and executions.topLevelItemCount():
                executions.setCurrentItem(executions.topLevelItem(0))

    def _populate_runs(
        self,
        tree: QTreeWidget,
        runs: tuple[ProjectRunInspection, ...],
        empty_message: str,
    ) -> None:
        tree.clear()
        if not runs:
            item = QTreeWidgetItem((empty_message, "empty", "—"))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            tree.addTopLevelItem(item)
            return
        tickets: dict[str, QTreeWidgetItem] = {}
        for run in runs:
            ticket_id = run.catalog_entry.ticket_id
            parent = tickets.get(ticket_id)
            if parent is None:
                parent = QTreeWidgetItem((ticket_id, "", ""))
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                tree.addTopLevelItem(parent)
                tickets[ticket_id] = parent
            if run.inspection is None:
                status, phase = run.availability, "Unavailable"
            else:
                status = run.inspection.state.status.value
                phase = run.inspection.state.phase
            child = QTreeWidgetItem((run_label(run), status, phase))
            child.setData(0, RUN_DATA, run)
            child.setData(0, Qt.ItemDataRole.AccessibleTextRole, run_label(run))
            parent.addChild(child)
        tree.expandAll()

    def _populate_intel(self, inspection: IntelInspection) -> None:
        self.intel_tree.clear()
        accepted = QTreeWidgetItem(("Accepted Intel", str(len(inspection.accepted))))
        candidates = QTreeWidgetItem(("Recon candidates", str(len(inspection.candidates))))
        for parent in (accepted, candidates):
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.intel_tree.addTopLevelItem(parent)
        for item in inspection.accepted:
            child = QTreeWidgetItem((item.instinct_id, "accepted"))
            child.setData(0, INTEL_DATA, item)
            accepted.addChild(child)
        entries = {entry.candidate.instinct_id: entry for entry in inspection.candidate_entries}
        for item in inspection.candidates:
            entry = entries.get(item.instinct_id)
            disposition = entry.disposition.value if entry is not None else "pending"
            child = QTreeWidgetItem((item.instinct_id, disposition))
            child.setData(0, INTEL_DATA, item)
            child.setData(1, INTEL_DATA, disposition)
            candidates.addChild(child)
        if intel_empty(inspection):
            empty = QTreeWidgetItem(("No Intel or Recon evidence", "empty"))
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            candidates.addChild(empty)
        self.intel_tree.expandAll()

    def _select_run(
        self,
        item: QTreeWidgetItem | None,
        executions: QTreeWidget,
        inspector: QPlainTextEdit,
    ) -> None:
        executions.clear()
        if item is None:
            return
        run = item.data(0, RUN_DATA)
        if not isinstance(run, ProjectRunInspection):
            return
        self.selected_run = run
        available = run.inspection is not None
        self.queue_button.setEnabled(available)
        self.resume_button.setEnabled(
            available and run.inspection.state.status is RunStatus.AWAITING_HUMAN
        )
        worker = None
        if run.inspection is not None:
            worker = self.controller.worker_for(run.inspection.run_id)
        inspector.setPlainText(render_run(run, worker))
        if run.inspection is None:
            return
        for index, execution in enumerate(
            run.inspection.state.execution_record.node_executions, start=1
        ):
            child = QTreeWidgetItem((
                f"Attempt {index}", execution.role, execution.phase, execution.outcome
            ))
            child.setData(0, EXECUTION_DATA, execution)
            child.setData(
                0,
                Qt.ItemDataRole.AccessibleTextRole,
                f"Attempt {index}, {execution.role}, {execution.phase}, {execution.outcome}",
            )
            executions.addTopLevelItem(child)

    @staticmethod
    def _select_execution(
        item: QTreeWidgetItem | None, inspector: QPlainTextEdit
    ) -> None:
        if item is None:
            return
        execution = item.data(0, EXECUTION_DATA)
        if execution is not None:
            inspector.setPlainText(render_execution(execution))

    def _select_intel(
        self, item: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if item is None:
            return
        instinct = item.data(0, INTEL_DATA)
        if instinct is not None:
            self.intel_inspector.setPlainText(render_intel_item(instinct))
        self.selected_candidate = instinct if isinstance(instinct, CandidateInstinct) else None
        pending = self.selected_candidate is not None and item.data(1, INTEL_DATA) == "pending"
        for button in (
            self.accept_candidate_button,
            self.edit_candidate_button,
            self.reject_candidate_button,
        ):
            button.setEnabled(pending)

    def _refresh_intervention_targets(self) -> None:
        self.intervention_target.clear()
        kind = self.intervention_kind.currentData()
        if kind == InterventionKind.DESIGN_DECISION:
            self.intervention_target.addItem("Architect", InterventionTarget.ARCHITECT)
            return
        self.intervention_target.addItem("Driver RED", InterventionTarget.DRIVER_RED)
        self.intervention_target.addItem("Driver GREEN", InterventionTarget.DRIVER_GREEN)
        self.intervention_target.addItem("Refactorer", InterventionTarget.REFACTORER)

    def _resume_selected_run(self) -> None:
        if self.selected_run is None or self.selected_run.inspection is None:
            return
        self.controller.resolve_and_resume(
            self.selected_run.inspection.run_id,
            self.actor_edit.text(),
            self.resolution_edit.text(),
        )

    def _queue_selected_intervention(self) -> None:
        if self.selected_run is None or self.selected_run.inspection is None:
            return
        self.controller.queue_intervention(
            self.selected_run.inspection.run_id,
            self.intervention_kind.currentData(),
            self.intervention_target.currentData(),
            self.intervention_text.text(),
            self.actor_edit.text(),
        )

    def _review_selected_candidate(self, action: ReviewAction) -> None:
        if self.selected_candidate is None:
            return
        self.controller.review_candidate(
            self.selected_candidate.instinct_id,
            action,
            self.intel_actor_edit.text(),
        )

    def _edit_selected_candidate(self) -> None:
        if self.selected_candidate is None:
            return
        recommendation, accepted = QInputDialog.getMultiLineText(
            self,
            "Edit and promote Recon candidate",
            "Recommendation",
            self.selected_candidate.recommendation,
        )
        if accepted and recommendation.strip():
            self.controller.review_candidate(
                self.selected_candidate.instinct_id,
                ReviewAction.EDIT_AND_ACCEPT,
                self.intel_actor_edit.text(),
                {"recommendation": recommendation.strip()},
            )

    def render_recovered_run(self, inspection) -> None:
        self.live_state.setText(
            f"Recovered durable state · {inspection.run_alias or inspection.run_id} · "
            f"{inspection.state.status.value} · {inspection.state.phase}"
        )

    def apply_observation(self, event: ObservationEvent) -> None:
        node = f" · {event.node}" if event.node else ""
        self.live_state.setText(
            f"Live · sequence {event.sequence} · {event.kind.value}{node}"
        )
        if event.kind is ObservationKind.STATE_CHECKPOINT:
            self.controller.refresh()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = BattalionWindow(args.project, autoload=args.screenshot is None)
    if args.screenshot is not None:
        try:
            window.render_snapshot(*window.controller.load_snapshot())
        except Exception as exc:  # Typed message belongs in the visible client surface.
            window.show_error(str(exc))
        window.show()
        application.processEvents()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError(f"Could not save desktop screenshot to {args.screenshot}")
        window.close()
        return 0
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
