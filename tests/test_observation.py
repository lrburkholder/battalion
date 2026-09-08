"""Focused BTN-36 live-observation contract tests."""

from __future__ import annotations

from support.state import make_run_state

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from battalion.application import (
    ReconnectObservation,
    StartRun,
    reconnect_observation,
    start_run,
)
from battalion.config import BattalionConfig
from battalion.observation import (
    ObservationBuffer,
    ObservationCategory,
    ObservationCursor,
    ObservationEvent,
    ObservationKind,
    RunObservationPublisher,
    ordered_unique_events,
)
from battalion.state.models import RunState, RunStatus


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def make_state(run_id: str, *, phase: str = "architect") -> RunState:
    return make_run_state(
        run_id=run_id,
        ticket_id='BTN-36',
        status=RunStatus.IN_PROGRESS,
        phase=phase,
        write_scope={},
        budget_limit=10,
    )


def test_publisher_adds_stable_run_attempt_order_and_timestamp_semantics():
    events = []
    publisher = RunObservationPublisher(
        "run-one", events.append, stream_id=uuid4(), clock=lambda: NOW
    )

    publisher.handle_node_event(
        {"type": "node_start", "node": "architect", "budget": {"used": 1}}
    )
    publisher.handle_token({"type": "token", "content": "plan"})
    publisher.handle_node_event(
        {"type": "node_end", "node": "architect", "phase": "driver_red"}
    )

    assert [event.sequence for event in events] == [1, 2, 3]
    assert len({event.event_id for event in events}) == 3
    assert {event.run_id for event in events} == {"run-one"}
    assert {event.stream_id for event in events} == {publisher.stream_id}
    assert {event.attempt_id for event in events} == {events[0].attempt_id}
    assert all(event.occurred_at == NOW for event in events)
    assert all(event.category == ObservationCategory.TRANSIENT for event in events)


def test_durable_transient_and_action_required_categories_are_explicit():
    events = []
    publisher = RunObservationPublisher("categorized", events.append, clock=lambda: NOW)
    publisher.handle_node_event({"type": "node_start", "node": "architect"})
    publisher.handle_node_event(
        {
            "type": "interrupt",
            "node": "architect",
            "trigger": "manual-checkpoint",
        }
    )
    publisher.handle_checkpoint(make_state("categorized", phase="awaiting_human"))

    assert [(event.kind, event.category) for event in events] == [
        (ObservationKind.NODE_STARTED, ObservationCategory.TRANSIENT),
        (ObservationKind.INTERRUPT, ObservationCategory.ACTION_REQUIRED),
        (ObservationKind.STATE_CHECKPOINT, ObservationCategory.DURABLE),
    ]
    assert events[-1].payload == {
        "state_version": "1.0",
        "status": "in-progress",
        "phase": "awaiting_human",
    }


def test_application_adapts_graph_callbacks_and_saves_before_durable_event(tmp_path):
    initial = make_state("application-run")
    events = []

    def emit(event):
        if event.kind == ObservationKind.STATE_CHECKPOINT:
            assert (tmp_path / "application-run.json").exists()
        events.append(event)

    def execute(**kwargs):
        kwargs["on_node_event"]({"type": "node_start", "node": "architect"})
        kwargs["on_token"]({"type": "token", "content": "plan"})
        progressed = kwargs["initial_state"].model_copy(
            update={"phase": "driver_red"}
        )
        kwargs["on_state_checkpoint"](progressed)
        kwargs["on_node_event"](
            {"type": "node_end", "node": "architect", "phase": "driver_red"}
        )
        return progressed

    start_run(
        StartRun(initial, BattalionConfig()),
        state_dir=tmp_path,
        on_observation=emit,
        _execute=execute,
    )

    assert [event.kind for event in events] == [
        ObservationKind.NODE_STARTED,
        ObservationKind.TOKEN,
        ObservationKind.STATE_CHECKPOINT,
        ObservationKind.NODE_FINISHED,
    ]


def test_multiple_runs_and_streams_order_independently_and_batch_deduplicates():
    first = []
    second = []
    one = RunObservationPublisher("run-one", first.append, clock=lambda: NOW)
    two = RunObservationPublisher("run-two", second.append, clock=lambda: NOW)
    one.handle_node_event({"type": "node_start", "node": "architect"})
    two.handle_node_event({"type": "node_start", "node": "driver_red"})
    one.handle_token({"type": "reasoning", "content": "thinking"})

    delivered = ordered_unique_events([first[1], second[0], first[0], first[0]])

    assert delivered == (first[0], first[1], second[0])


def test_buffer_ignores_duplicate_delivery_and_rejects_conflicting_duplicates():
    buffer = ObservationBuffer()
    events = []
    publisher = RunObservationPublisher("duplicates", events.append, clock=lambda: NOW)
    publisher.handle_node_event({"type": "node_start", "node": "architect"})
    buffer.publish(events[0])
    buffer.publish(events[0])

    assert buffer.after(
        ObservationCursor(
            run_id="duplicates", stream_id=publisher.stream_id, sequence=0
        )
    ) == (events[0],)

    conflicting = events[0].model_dump()
    conflicting["payload"] = {"type": "node_start", "node": "driver_red"}
    with pytest.raises(ValueError, match="conflicting content"):
        buffer.publish(conflicting)


def test_missed_transient_events_do_not_prevent_durable_recovery(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    durable = make_state("lossy-run", phase="driver_green")
    (state_dir / "lossy-run.json").write_text(
        durable.model_dump_json(), encoding="utf-8"
    )
    buffer = ObservationBuffer(max_events_per_stream=1)
    publisher = RunObservationPublisher("lossy-run", buffer.publish, clock=lambda: NOW)
    publisher.handle_node_event({"type": "node_start", "node": "driver_red"})
    publisher.handle_token({"type": "token", "content": "dropped predecessor"})

    snapshot = reconnect_observation(
        ReconnectObservation("lossy-run", publisher.stream_id),
        buffer,
        state_dir=state_dir,
    )

    assert snapshot.inspection.state == durable
    assert snapshot.cursor.sequence == 2
    assert buffer.after(snapshot.cursor) == ()


def test_reconnect_loads_authoritative_state_before_newer_events_are_consumed(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    durable = make_state("reconnect-run", phase="reviewer_red")
    (state_dir / "reconnect-run.json").write_text(
        durable.model_dump_json(), encoding="utf-8"
    )
    buffer = ObservationBuffer()
    publisher = RunObservationPublisher(
        "reconnect-run", buffer.publish, clock=lambda: NOW
    )
    publisher.handle_node_event({"type": "node_start", "node": "driver_red"})

    snapshot = reconnect_observation(
        ReconnectObservation("reconnect-run", publisher.stream_id),
        buffer,
        state_dir=state_dir,
    )
    publisher.handle_token({"type": "token", "content": "newer"})

    assert snapshot.inspection.state.phase == "reviewer_red"
    assert snapshot.cursor.sequence == 1
    assert [event.sequence for event in buffer.after(snapshot.cursor)] == [2]


def test_malformed_events_and_invalid_raw_callbacks_fail_without_state_mutation():
    stream_id = uuid4()
    malformed = {
        "event_id": uuid4(),
        "run_id": "run",
        "stream_id": stream_id,
        "sequence": 1,
        "occurred_at": "2026-08-17T12:00:00",
        "category": "durable",
        "kind": "token",
        "node": "architect",
        "attempt_id": uuid4(),
    }
    with pytest.raises(ValidationError):
        ObservationEvent.model_validate(malformed)

    publisher = RunObservationPublisher("run", lambda event: None)
    with pytest.raises(ValueError, match="active node attempt"):
        publisher.handle_token({"type": "token", "content": "orphan"})

    state = make_state("run")
    original = state.model_dump()
    publisher.handle_checkpoint(state)
    assert state.model_dump() == original
