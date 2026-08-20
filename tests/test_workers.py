"""Focused BTN-31 worker supervision tests; no provider calls are made."""

from __future__ import annotations

import io
import json
from dataclasses import replace

import pytest

from battalion.application import (
    CancelWorker,
    ObserveWorker,
    ReconnectWorker,
    StartRun,
    StartWorker,
    WorkerAlreadyActive,
    WorkerRecordReadFailed,
    cancel_worker,
    observe_worker,
    reconnect_worker,
    start_worker,
)
from battalion.config import BattalionConfig
from battalion.state.models import Budget, RunState, RunStatus
from battalion.state.persistence import load_state
from battalion.workers import (
    WorkerRecord,
    WorkerStatus,
    _read_record,
    _worker_main,
    _write_record,
)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.stdin = io.BytesIO()


def make_state(run_id: str) -> RunState:
    return RunState(
        schema_version="1.0",
        run_id=run_id,
        ticket_id="BTN-31-test",
        status=RunStatus.NOT_STARTED,
        phase="architect",
        retry_bound=2,
        budget=Budget(limit=10),
    )


def test_concurrent_runs_get_distinct_processes_and_durable_associations(
    tmp_path, monkeypatch
):
    processes = iter([FakeProcess(3101), FakeProcess(3102)])
    monkeypatch.setattr("battalion.workers._spawn_process", lambda: next(processes))
    state_dir = tmp_path / "state"
    worker_dir = tmp_path / "workers"

    first = start_worker(
        StartWorker(StartRun(make_state("run-one"), BattalionConfig())),
        state_dir=state_dir,
        worker_dir=worker_dir,
    )
    second = start_worker(
        StartWorker(StartRun(make_state("run-two"), BattalionConfig())),
        state_dir=state_dir,
        worker_dir=worker_dir,
    )

    assert first.pid == 3101
    assert second.pid == 3102
    assert first.worker_id != second.worker_id
    assert _read_record(worker_dir / "run-one.json") == first
    assert _read_record(worker_dir / "run-two.json") == second
    assert load_state(state_dir / "run-one.json").status == RunStatus.IN_PROGRESS


def test_one_active_worker_is_allowed_per_canonical_run(tmp_path, monkeypatch):
    monkeypatch.setattr("battalion.workers._spawn_process", lambda: FakeProcess(3103))
    monkeypatch.setattr("battalion.workers._pid_exists", lambda pid: True)
    command = StartWorker(StartRun(make_state("same-run"), BattalionConfig()))
    kwargs = {"state_dir": tmp_path / "state", "worker_dir": tmp_path / "workers"}
    start_worker(command, **kwargs)

    with pytest.raises(WorkerAlreadyActive):
        start_worker(
            StartWorker(
                StartRun(make_state("same-run"), BattalionConfig(), overwrite=True)
            ),
            **kwargs,
        )


def test_reconnect_marks_abnormal_exit_recoverable_from_durable_state(
    tmp_path, monkeypatch
):
    worker_dir = tmp_path / "workers"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state = make_state("crashed-run").model_copy(
        update={"status": RunStatus.IN_PROGRESS}
    )
    (state_dir / "crashed-run.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )
    record = WorkerRecord(
        run_id="crashed-run",
        state_version="1.0",
        worker_id="worker-token",
        operation="start",
        status=WorkerStatus.RUNNING,
        pid=999999,
        started_at="2026-08-14T00:00:00+00:00",
        updated_at="2026-08-14T00:00:00+00:00",
        state_path=str(state_dir / "crashed-run.json"),
    )
    _write_record(worker_dir / "crashed-run.json", record)
    monkeypatch.setattr("battalion.workers._pid_exists", lambda pid: False)

    reconnected = reconnect_worker(
        ReconnectWorker("crashed-run"), worker_dir=worker_dir
    )

    assert reconnected.status == WorkerStatus.CRASHED
    assert reconnected.recoverable is True
    assert reconnected.error == "Worker exited abnormally"


def test_cancel_targets_only_the_selected_worker(tmp_path, monkeypatch):
    worker_dir = tmp_path / "workers"
    records = []
    for run_id, pid in (("selected", 4101), ("unrelated", 4102)):
        record = WorkerRecord(
            run_id=run_id,
            state_version="1.0",
            worker_id=f"worker-{run_id}",
            operation="start",
            status=WorkerStatus.RUNNING,
            pid=pid,
            started_at="2026-08-14T00:00:00+00:00",
            updated_at="2026-08-14T00:00:00+00:00",
            state_path=str(tmp_path / "state" / f"{run_id}.json"),
        )
        _write_record(worker_dir / f"{run_id}.json", record)
        records.append(record)
    killed = []
    monkeypatch.setattr("battalion.workers._pid_exists", lambda pid: True)
    monkeypatch.setattr("battalion.workers.os.kill", lambda pid, sig: killed.append(pid))

    result = cancel_worker(CancelWorker("selected"), worker_dir=worker_dir)

    assert result.status == WorkerStatus.CANCELLING
    assert result.cancellation_requested is True
    assert killed == [4101]
    assert observe_worker(
        ObserveWorker("unrelated"), worker_dir=worker_dir
    ).status == WorkerStatus.RUNNING


def test_completed_record_reconnects_without_a_live_process(tmp_path, monkeypatch):
    worker_dir = tmp_path / "workers"
    record = WorkerRecord(
        run_id="done-run",
        state_version="1.0",
        worker_id="worker-done",
        operation="start",
        status=WorkerStatus.SUCCEEDED,
        pid=5101,
        started_at="2026-08-14T00:00:00+00:00",
        updated_at="2026-08-14T00:01:00+00:00",
        state_path=str(tmp_path / "state" / "done-run.json"),
    )
    _write_record(worker_dir / "done-run.json", record)
    monkeypatch.setattr(
        "battalion.workers._pid_exists",
        lambda pid: pytest.fail("terminal workers must not be probed"),
    )

    assert reconnect_worker(
        ReconnectWorker("done-run"), worker_dir=worker_dir
    ) == record


def test_worker_process_is_detached_from_presentation_client(monkeypatch):
    captured = {}

    def popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProcess(6101)

    monkeypatch.setattr("battalion.workers.subprocess.Popen", popen)

    from battalion.workers import _spawn_process

    _spawn_process()

    assert captured["argv"][-3:] == ["-m", "battalion.workers", "run"]
    assert captured["close_fds"] is True
    if "creationflags" in captured:
        assert captured["creationflags"]
    else:
        assert captured["start_new_session"] is True


def test_frozen_desktop_launches_sibling_worker_distribution(tmp_path, monkeypatch):
    captured = {}

    def popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeProcess(6102)

    monkeypatch.setattr("battalion.workers.subprocess.Popen", popen)
    monkeypatch.setattr("battalion.workers.__compiled__", object(), raising=False)
    desktop = tmp_path / "Battalion.dist" / "Battalion.exe"
    desktop.parent.mkdir()
    desktop.touch()
    worker = (
        tmp_path / "worker" / "worker_entry.dist" / "BattalionWorker.exe"
    )
    worker.parent.mkdir(parents=True)
    worker.touch()
    monkeypatch.setattr("battalion.workers.sys.executable", str(desktop))

    from battalion.workers import _spawn_process

    _spawn_process()

    assert captured["argv"] == [str(worker)]


def test_frozen_desktop_reports_missing_split_worker(tmp_path, monkeypatch):
    desktop = tmp_path / "Battalion.dist" / "Battalion.exe"
    desktop.parent.mkdir()
    desktop.touch()
    monkeypatch.setattr("battalion.workers.__compiled__", object(), raising=False)
    monkeypatch.setattr("battalion.workers.sys.executable", str(desktop))

    from battalion.workers import _spawn_process

    with pytest.raises(FileNotFoundError, match="split worker distribution"):
        _spawn_process()


def test_worker_entrypoint_executes_application_boundary_and_records_completion(
    tmp_path, monkeypatch
):
    state = make_state("entrypoint-run")
    worker_dir = tmp_path / "workers"
    record = WorkerRecord(
        run_id=state.run_id,
        state_version=state.schema_version,
        worker_id="entrypoint-worker",
        operation="start",
        status=WorkerStatus.STARTING,
        pid=7101,
        started_at="2026-08-14T00:00:00+00:00",
        updated_at="2026-08-14T00:00:00+00:00",
        state_path=str(tmp_path / "state" / "entrypoint-run.json"),
    )
    _write_record(worker_dir / "entrypoint-run.json", record)
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)

    monkeypatch.setattr("battalion.application.start_run", run)
    request = {
        "operation": "start",
        "run_id": state.run_id,
        "worker_id": record.worker_id,
        "state": state.model_dump(mode="json"),
        "config": BattalionConfig().model_dump(mode="json"),
        "state_dir": str(tmp_path / "state"),
        "worker_dir": str(worker_dir),
    }

    exit_code = _worker_main(io.BytesIO(json.dumps(request).encode("utf-8")))

    assert exit_code == 0
    assert captured["command"].initial_state == state
    assert captured["command"].overwrite is True
    assert _read_record(worker_dir / "entrypoint-run.json").status == WorkerStatus.SUCCEEDED


def test_malformed_worker_record_is_a_typed_application_failure(tmp_path):
    worker_dir = tmp_path / "workers"
    worker_dir.mkdir()
    (worker_dir / "malformed.json").write_text("not json", encoding="utf-8")

    with pytest.raises(WorkerRecordReadFailed) as raised:
        observe_worker(ObserveWorker("malformed"), worker_dir=worker_dir)

    assert raised.value.run_id == "malformed"
