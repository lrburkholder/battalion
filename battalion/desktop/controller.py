"""Desktop orchestration over the transport-neutral application boundary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import UUID

from PySide6.QtCore import QObject, QThreadPool, Signal

from battalion.application import (
    ApplicationError,
    InspectIntel,
    InspectProject,
    IntelInspection,
    ProjectInspection,
    ReconnectObservation,
    ReconnectWorker,
    WorkerNotFound,
    inspect_intel,
    inspect_project,
    reconnect_observation,
    reconnect_worker,
)
from battalion.observation import ObservationCursor, ObservationEvent, ObservationSource
from battalion.workers import WorkerRecord


class DesktopObservationSource(ObservationSource, Protocol):
    """Observation transport capability consumed by the desktop client."""

    def after(self, cursor: ObservationCursor) -> tuple[ObservationEvent, ...]: ...


class DesktopController(QObject):
    """Runs application queries off the Qt UI thread and emits typed results."""

    loading = Signal()
    snapshot_ready = Signal(object, object)
    load_failed = Signal(str)
    durable_recovered = Signal(object)
    live_observation = Signal(object)

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
