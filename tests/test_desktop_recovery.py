"""Desktop restart recovery and real worker exit observation."""


from __future__ import annotations


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


import subprocess
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5
from battalion.desktop.app import BattalionWindow
from battalion.desktop.controller import DesktopController
from battalion.identity import load_project_identity
from battalion.observation import (
    ObservationCategory,
    ObservationCursor,
    ObservationEvent,
    ObservationKind,
)
from battalion.state.models import Budget, RunState, RunStatus
from battalion.workers import WorkerRecord, WorkerStatus, _write_record
from support.desktop import (
    qt_app,
)


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
