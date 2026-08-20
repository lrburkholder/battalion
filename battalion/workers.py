"""Isolated, durable supervision for active Battalion runs.

The supervisor is deliberately small and non-authoritative: one detached Python
process executes one run, while JSON metadata and the canonical ``RunState``
remain the reconnect and recovery mechanisms.  Presentation clients never need
to retain a ``Popen`` object in order to observe a worker after reconnecting.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from battalion.config import BattalionConfig
from battalion.state.models import RunState, RunStatus
from battalion.state.persistence import save_state


DEFAULT_WORKER_DIR = Path(".battalion/workers")


class WorkerStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CRASHED = "crashed"


ACTIVE_WORKER_STATUSES = {
    WorkerStatus.STARTING,
    WorkerStatus.RUNNING,
    WorkerStatus.CANCELLING,
}


@dataclass(frozen=True)
class WorkerRecord:
    run_id: str
    state_version: str
    worker_id: str
    operation: str
    status: WorkerStatus
    pid: int | None
    started_at: str
    updated_at: str
    state_path: str
    error: str | None = None
    cancellation_requested: bool = False

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_WORKER_STATUSES

    @property
    def recoverable(self) -> bool:
        return self.status == WorkerStatus.CRASHED and Path(self.state_path).exists()


class WorkerError(Exception):
    """Base class for expected worker-supervision failures."""


class WorkerNotFound(WorkerError):
    pass


class WorkerAlreadyActive(WorkerError):
    pass


class WorkerLaunchFailed(WorkerError):
    pass


def launch_worker(
    *,
    operation: str,
    run_id: str,
    state: RunState | None,
    state_version: str,
    config: BattalionConfig,
    state_dir: str | Path,
    worker_dir: str | Path = DEFAULT_WORKER_DIR,
) -> WorkerRecord:
    """Launch one detached Python worker after durably reserving ``run_id``."""
    if operation not in {"start", "resume"}:
        raise ValueError(f"Unsupported worker operation: {operation!r}")

    directory = Path(worker_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record_path = _record_path(run_id, directory)
    worker_id = str(uuid4())
    now = _now()
    target_state_path = Path(state_dir) / f"{run_id}.json"
    record = WorkerRecord(
        run_id=run_id,
        state_version=state_version,
        worker_id=worker_id,
        operation=operation,
        status=WorkerStatus.STARTING,
        pid=None,
        started_at=now,
        updated_at=now,
        state_path=str(target_state_path),
    )
    lock_path = record_path.with_suffix(record_path.suffix + ".launch.lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise WorkerAlreadyActive(
            f"Run {run_id!r} already has a worker launch in progress"
        ) from exc
    try:
        # The exclusive launch lock closes the small race between checking an
        # old terminal record and reserving the new active association.
        if record_path.exists():
            existing = reconnect_worker(run_id, worker_dir=directory)
            if existing.active:
                raise WorkerAlreadyActive(
                    f"Run {run_id!r} already has an active worker"
                )
        _write_record(record_path, record)
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)

    # A start request gets a durable baseline before provider or graph work.
    # If the worker dies, reconnect can distinguish recovery from total loss.
    if operation == "start":
        if state is None:
            raise ValueError("A start worker requires an initial state")
        save_state(
            state.model_copy(update={"status": RunStatus.IN_PROGRESS}),
            target_state_path,
        )

    request = {
        "operation": operation,
        "run_id": run_id,
        "worker_id": worker_id,
        "state": state.model_dump(mode="json") if state is not None else None,
        "config": config.model_dump(mode="json"),
        "state_dir": str(state_dir),
        "worker_dir": str(directory),
    }
    try:
        process = _spawn_process()
        launched = _replace(record, pid=process.pid)
        _write_record(record_path, launched)
        assert process.stdin is not None
        process.stdin.write(json.dumps(request).encode("utf-8"))
        process.stdin.close()
    except (OSError, BrokenPipeError, TypeError, ValueError) as exc:
        failed = _replace(record, status=WorkerStatus.FAILED, error=str(exc))
        _write_record(record_path, failed)
        raise WorkerLaunchFailed(f"Could not launch worker for {run_id}: {exc}") from exc
    return launched


def observe_worker(
    run_id: str, *, worker_dir: str | Path = DEFAULT_WORKER_DIR
) -> WorkerRecord:
    """Return durable worker state, reconciling an unreported process death."""
    path = _record_path(run_id, worker_dir)
    if not path.exists():
        raise WorkerNotFound(f"No worker record for run {run_id!r}")
    record = _read_record(path)
    if record.active and record.pid is not None and not _pid_exists(record.pid):
        terminal = (
            WorkerStatus.CANCELLED
            if record.cancellation_requested
            else WorkerStatus.CRASHED
        )
        record = _replace(
            record,
            status=terminal,
            error=record.error or (
                None if terminal == WorkerStatus.CANCELLED else "Worker exited abnormally"
            ),
        )
        _write_record(path, record)
    return record


def reconnect_worker(
    run_id: str, *, worker_dir: str | Path = DEFAULT_WORKER_DIR
) -> WorkerRecord:
    """Reconnect from durable metadata; no original process handle is needed."""
    return observe_worker(run_id, worker_dir=worker_dir)


def cancel_worker(
    run_id: str, *, worker_dir: str | Path = DEFAULT_WORKER_DIR
) -> WorkerRecord:
    """Request cancellation of exactly the process associated with ``run_id``."""
    path = _record_path(run_id, worker_dir)
    record = observe_worker(run_id, worker_dir=worker_dir)
    if not record.active or record.pid is None:
        return record

    cancelling = _replace(
        record,
        status=WorkerStatus.CANCELLING,
        cancellation_requested=True,
    )
    _write_record(path, cancelling)
    try:
        os.kill(record.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        failed = _replace(cancelling, status=WorkerStatus.FAILED, error=str(exc))
        _write_record(path, failed)
        return failed
    return cancelling


def _spawn_process() -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, "-m", "battalion.workers", "run"],
        **kwargs,
    )


def _worker_main(stdin: BinaryIO) -> int:
    request = json.loads(stdin.read().decode("utf-8"))
    run_id = request["run_id"]
    worker_id = request["worker_id"]
    record_path = _record_path(run_id, request["worker_dir"])
    current = _read_record(record_path)
    if current.worker_id != worker_id:
        return 2

    running = _replace(current, status=WorkerStatus.RUNNING, pid=os.getpid())
    _write_record(record_path, running)
    try:
        from battalion.application import ResumeRun, StartRun, resume_run, start_run

        config = BattalionConfig.model_validate(request["config"])
        if request["operation"] == "start":
            state = RunState.model_validate(request["state"])
            start_run(
                StartRun(initial_state=state, config=config, overwrite=True),
                state_dir=request["state_dir"],
            )
        else:
            resume_run(
                ResumeRun(run_id=run_id, config=config),
                state_dir=request["state_dir"],
            )
    except BaseException as exc:
        latest = _read_record(record_path)
        status = (
            WorkerStatus.CANCELLED
            if latest.cancellation_requested
            else WorkerStatus.FAILED
        )
        _write_record(record_path, _replace(latest, status=status, error=str(exc)))
        return 1

    latest = _read_record(record_path)
    status = (
        WorkerStatus.CANCELLED
        if latest.cancellation_requested
        else WorkerStatus.SUCCEEDED
    )
    _write_record(record_path, _replace(latest, status=status))
    return 0


def _record_path(run_id: str, worker_dir: str | Path) -> Path:
    # Application validation guarantees a non-path identifier before launch.
    return Path(worker_dir) / f"{run_id}.json"


def _read_record(path: Path) -> WorkerRecord:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["status"] = WorkerStatus(raw["status"])
    return WorkerRecord(**raw)


def _write_record(path: Path, record: WorkerRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    payload["status"] = record.status.value
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _replace(record: WorkerRecord, **changes: Any) -> WorkerRecord:
    values = asdict(record)
    values.update(changes)
    values["updated_at"] = _now()
    return WorkerRecord(**values)


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "run":
        raise SystemExit("usage: python -m battalion.workers run")
    raise SystemExit(_worker_main(sys.stdin.buffer))
