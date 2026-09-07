"""Tests for battalion.state.persistence — local JSON save/load (BTN-1)."""
from support.state import make_run_state

import json

import pytest
from pydantic import ValidationError

from battalion.state.models import InterruptLogEntry, RunState, RunStatus
from battalion.state.persistence import load_state, save_state


def make_state():
    return make_run_state(
        run_id='run-001',
        ticket_id='BTN-1',
        spec='Persist this specification across invocations.',
        status=RunStatus.IN_PROGRESS,
        phase='driver',
        write_scope={'driver': ['src/']},
        budget_used=5,
    )


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "run-001.json"
    state = make_state()

    save_state(state, path)
    loaded = load_state(path)

    assert loaded == state


def test_save_writes_valid_json(tmp_path):
    path = tmp_path / "run-001.json"
    save_state(make_state(), path)

    with open(path) as f:
        raw = json.load(f)  # must not raise
    assert raw["run_id"] == "run-001"


def test_load_missing_file_raises(tmp_path):
    path = tmp_path / "does-not-exist.json"
    with pytest.raises(FileNotFoundError):
        load_state(path)


def test_load_malformed_json_raises_clear_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json")

    with pytest.raises(ValueError):
        load_state(path)


def test_load_invalid_state_shape_raises_validation_error(tmp_path):
    path = tmp_path / "invalid-shape.json"
    path.write_text(json.dumps({"run_id": "run-001"}))  # missing required fields

    with pytest.raises(ValidationError):
        load_state(path)


def test_second_invocation_resumes_from_saved_state(tmp_path):
    """Simulates: a CLI run persists state, process exits, a second
    invocation loads it back and continues."""
    path = tmp_path / "run-001.json"
    original = make_state()
    save_state(original, path)

    resumed = load_state(path)
    assert resumed.status == RunStatus.IN_PROGRESS
    assert resumed.phase == "driver"
    assert resumed.spec == "Persist this specification across invocations."


def test_interrupt_log_with_entry_round_trips(tmp_path):
    path = tmp_path / "run-002.json"
    state = make_state()
    state.interrupt_log.append(
        InterruptLogEntry(
            trigger="budget-exceeded",
            timestamp="2026-07-27T12:00:00Z",
            resolution=None,
        )
    )

    save_state(state, path)
    loaded = load_state(path)

    assert len(loaded.interrupt_log) == 1
    assert loaded.interrupt_log[0].trigger == "budget-exceeded"
