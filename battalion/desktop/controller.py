"""Desktop orchestration over the transport-neutral application boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from PySide6.QtCore import QObject, QThreadPool, Signal

from battalion.application import (
    ApplicationError,
    AssessWorkflowAdmission,
    CreateAdmittedRun,
    DecideWorkflowAdmission,
    InspectIntel,
    InspectProject,
    InspectWorkflowAdmission,
    IntelInspection,
    ProjectInspection,
    ReconnectObservation,
    ReconnectWorker,
    QueueIntervention,
    ResumeRun,
    ReviewCandidate,
    RunOperationResult,
    StartWorker,
    WorkerNotFound,
    WorkflowAdmissionDecisionResult,
    WorkflowAdmissionInspection,
    assess_workflow_admission,
    create_admitted_run,
    decide_workflow_admission,
    inspect_intel,
    inspect_project,
    inspect_workflow_admission,
    queue_intervention,
    reconnect_observation,
    reconnect_worker,
    review_candidate,
    start_worker,
)
from battalion.config import load_config
from battalion.intel.review import ReviewAction
from battalion.observation import ObservationCursor, ObservationEvent, ObservationSource
from battalion.state.models import InterventionKind, InterventionTarget
from battalion.tactician import TacticianAssessment
from battalion.workflow_admission import (
    WorkflowAdmissionAssessment,
    WorkflowAdmissionEvidence,
)
from battalion.workflow_admission_decisions import WorkflowAdmissionDisposition
from battalion.workers import WorkerRecord


class DesktopObservationSource(ObservationSource, Protocol):
    """Observation transport capability consumed by the desktop client."""

    def after(self, cursor: ObservationCursor) -> tuple[ObservationEvent, ...]: ...


@dataclass(frozen=True)
class DesktopAdmissionSession:
    """One immutable desktop view of current pre-admission evidence."""

    ticket_id: str
    spec: str
    evidence: WorkflowAdmissionEvidence
    assessment: WorkflowAdmissionAssessment
    tactician_assessment: TacticianAssessment | None
    inspection: WorkflowAdmissionInspection


class DesktopController(QObject):
    """Runs application queries off the Qt UI thread and emits typed results."""

    loading = Signal()
    snapshot_ready = Signal(object, object)
    load_failed = Signal(str)
    durable_recovered = Signal(object)
    live_observation = Signal(object)
    action_completed = Signal(str)
    action_failed = Signal(str)
    admission_ready = Signal(object)

    def __init__(
        self,
        project_root: str | Path,
        *,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()

    def load_snapshot(self) -> tuple[ProjectInspection, IntelInspection]:
        """Load one authoritative, read-only snapshot through application queries."""
        project = inspect_project(InspectProject(self.project_root))
        intel = inspect_intel(InspectIntel(self.project_root))
        return project, intel

    def refresh(self) -> None:
        """Refresh without blocking Qt's event loop."""
        self.loading.emit()

        def query() -> None:
            try:
                project, intel = self.load_snapshot()
            except (ApplicationError, OSError, ValueError) as exc:
                self.load_failed.emit(str(exc))
                return
            self.snapshot_ready.emit(project, intel)

        self.thread_pool.start(query)

    def worker_for(self, run_id: str) -> WorkerRecord | None:
        """Reconnect to durable worker metadata after a client restart."""
        try:
            return reconnect_worker(
                ReconnectWorker(run_id),
                worker_dir=self.project_root / ".battalion" / "workers",
            )
        except WorkerNotFound:
            return None

    def recover_live(
        self,
        run_id: str,
        stream_id: UUID,
        source: DesktopObservationSource,
    ) -> None:
        """Render durable truth before consuming events newer than its barrier."""
        self.loading.emit()

        def recover() -> None:
            try:
                snapshot = reconnect_observation(
                    ReconnectObservation(run_id, stream_id),
                    source,
                    state_dir=self.project_root / ".battalion" / "state",
                )
                self.durable_recovered.emit(snapshot.inspection)
                for event in source.after(snapshot.cursor):
                    self.live_observation.emit(event)
            except (ApplicationError, OSError, ValueError) as exc:
                self.load_failed.emit(str(exc))

        self.thread_pool.start(recover)

    def subscribe(
        self,
        connect: Callable[[Callable[[ObservationEvent], None]], None],
    ) -> None:
        """Attach a transport without granting it control or persistence access."""
        connect(self.live_observation.emit)

    def _config(self):
        path = self.project_root / "battalion.config.yaml"
        return load_config(
            path if path.exists() else None,
            {"base_dir": str(self.project_root)},
        )

    def prepare_admission(
        self,
        ticket_id: str,
        spec: str,
        evidence: WorkflowAdmissionEvidence,
        tactician_assessment: TacticianAssessment | None = None,
    ) -> DesktopAdmissionSession:
        """Build the same application-owned decision surface used by CLI."""

        assessment = assess_workflow_admission(
            AssessWorkflowAdmission(evidence=evidence)
        )
        inspection = inspect_workflow_admission(
            InspectWorkflowAdmission(
                assessment=assessment,
                evidence=evidence,
                tactician_assessment=tactician_assessment,
            )
        )
        return DesktopAdmissionSession(
            ticket_id=ticket_id,
            spec=spec,
            evidence=evidence,
            assessment=assessment,
            tactician_assessment=tactician_assessment,
            inspection=inspection,
        )

    def load_admission_files(
        self,
        ticket_id: str,
        spec_or_path: str,
        evidence_path: str,
        tactician_path: str | None = None,
    ) -> None:
        """Load bounded JSON inputs off the UI thread and emit one typed session."""

        self.loading.emit()

        def query() -> None:
            try:
                spec_path = Path(spec_or_path)
                spec = (
                    spec_path.read_text(encoding="utf-8")
                    if spec_path.exists()
                    else spec_or_path
                )
                evidence = WorkflowAdmissionEvidence.model_validate_json(
                    Path(evidence_path).read_text(encoding="utf-8")
                )
                tactician = (
                    TacticianAssessment.model_validate_json(
                        Path(tactician_path).read_text(encoding="utf-8")
                    )
                    if tactician_path
                    else None
                )
                session = self.prepare_admission(ticket_id, spec, evidence, tactician)
            except (ApplicationError, OSError, TypeError, ValueError) as exc:
                self.action_failed.emit(str(exc))
                return
            self.admission_ready.emit(session)

        self.thread_pool.start(query)

    def decide_admission(
        self,
        session: DesktopAdmissionSession,
        disposition: WorkflowAdmissionDisposition,
        annotation: str | None = None,
    ) -> WorkflowAdmissionDecisionResult | RunOperationResult:
        """Apply one authorized choice through the shared application boundary."""

        common = {
            "assessment": session.assessment,
            "evidence": session.evidence,
            "disposition": disposition,
            "tactician_assessment": session.tactician_assessment,
            "annotation": annotation,
        }
        if disposition in {
            WorkflowAdmissionDisposition.FULL,
            WorkflowAdmissionDisposition.COMPACT,
        }:
            return create_admitted_run(
                CreateAdmittedRun(
                    ticket_id=session.ticket_id,
                    spec=session.spec,
                    config=self._config(),
                    **common,
                ),
                state_dir=self.project_root / ".battalion" / "state",
            )
        return decide_workflow_admission(
            DecideWorkflowAdmission(project_root=self.project_root, **common)
        )

    def submit_admission_decision(
        self,
        session: DesktopAdmissionSession,
        disposition: WorkflowAdmissionDisposition,
        annotation: str | None = None,
    ) -> None:
        """Record a desktop decision without blocking Qt's event loop."""

        def command() -> None:
            try:
                result = self.decide_admission(session, disposition, annotation)
            except (ApplicationError, OSError, TypeError, ValueError) as exc:
                self.action_failed.emit(str(exc))
                return
            if isinstance(result, WorkflowAdmissionDecisionResult):
                decision = result.decision
            else:
                record = result.state.workflow_admission
                assert record is not None
                decision = record.decision
            self.action_completed.emit(
                f"Workflow admission {decision.disposition.value} authorized"
            )
            if decision.disposition in {
                WorkflowAdmissionDisposition.FULL,
                WorkflowAdmissionDisposition.COMPACT,
            }:
                self.refresh()

        self.thread_pool.start(command)

    def resolve_and_resume(self, run_id: str, resolution: str) -> None:
        """Resolve and resume through the same worker/application path as CLI."""
        def command() -> None:
            try:
                start_worker(
                    StartWorker(ResumeRun(
                        run_id=run_id,
                        config=self._config(),
                        resolution=resolution,
                    )),
                    state_dir=self.project_root / ".battalion" / "state",
                    worker_dir=self.project_root / ".battalion" / "workers",
                )
            except (ApplicationError, OSError, TypeError, ValueError) as exc:
                self.action_failed.emit(str(exc))
                return
            self.action_completed.emit(f"Resume started for {run_id}")
            self.refresh()

        self.thread_pool.start(command)

    def queue_intervention(
        self,
        run_id: str,
        kind: InterventionKind,
        target: InterventionTarget,
        text: str,
    ) -> None:
        def command() -> None:
            try:
                result = queue_intervention(
                    QueueIntervention(
                        run_id,
                        kind,
                        target,
                        text,
                        project_root=self.project_root,
                    ),
                    state_dir=self.project_root / ".battalion" / "state",
                    worker_dir=self.project_root / ".battalion" / "workers",
                )
            except (ApplicationError, OSError, TypeError, ValueError) as exc:
                self.action_failed.emit(str(exc))
                return
            self.action_completed.emit(
                f"{result.action.kind} queued for {result.action.target}"
            )
            self.refresh()

        self.thread_pool.start(command)

    def review_candidate(
        self,
        candidate_id: str,
        action: ReviewAction,
        edits: dict[str, object] | None = None,
    ) -> None:
        def command() -> None:
            try:
                result = review_candidate(ReviewCandidate(
                    project_root=self.project_root,
                    candidate_id=candidate_id,
                    action=action,
                    edits=edits,
                ))
            except (ApplicationError, OSError, TypeError, ValueError) as exc:
                self.action_failed.emit(str(exc))
                return
            self.action_completed.emit(
                f"{candidate_id} {result.disposition} by {result.decision.decided_by}"
            )
            self.refresh()

        self.thread_pool.start(command)
