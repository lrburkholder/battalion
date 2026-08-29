"""BTN-73: versioned, minimized outbound machine-event delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

import pytest

from battalion.application import StartRun, start_run
from battalion.config import BattalionConfig
from battalion.integrations.configuration import (
    CapabilitySurface,
    IntegrationConfiguration,
    TransportKind,
)
from battalion.integrations.events import (
    EventRunProvenance,
    OutboundEvent,
    OutboundEventPublisher,
    OutboundEventType,
    RunCompletedData,
    events_for_state,
)
from battalion.integrations.runtime import (
    AdapterRegistration,
    IntegrationRuntime,
    IntegrationTimeout,
)
from battalion.state.models import Budget, InterruptLogEntry, RunState, RunStatus


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _state(*, status: RunStatus, phase: str = "done") -> RunState:
    return RunState(
        schema_version="1.0",
        run_id="run-btn-73",
        run_alias="BTN-73-slate",
        project_id="30000000-0000-4000-8000-000000000073",
        ticket_id="BTN-73",
        spec="Outbound event test.",
        status=status,
        phase=phase,
        write_scope={},
        retry_bound=2,
        budget=Budget(limit=10),
    )


@dataclass
class _Sink:
    integration_id: str
    integration_name: str
    provider: str = "fixture-sink"
    transport: TransportKind = TransportKind.WEBHOOK
    events: list[tuple[OutboundEvent, str]] = field(default_factory=list)
    failure: Exception | None = None
    capability: CapabilitySurface = CapabilitySurface.OUTBOUND_EVENT_SINK

    def publish(self, event: OutboundEvent, *, idempotency_key: str) -> bool:
        if self.failure is not None:
            raise self.failure
        self.events.append((event, idempotency_key))
        return True


class _FixtureTransport:
    def invoke(self, operation, call):  # pragma: no cover - fixture has no IO
        raise AssertionError("outbound-event fixture bypasses the transport")


def _runtime(*sinks: _Sink) -> IntegrationRuntime:
    configuration = IntegrationConfiguration.model_validate(
        {
            "project": {
                "integrations": {
                    sink.integration_name: {
                        "integration_id": sink.integration_id,
                        "provider": "fixture-sink",
                        "transport": "webhook",
                        "capabilities": ["outbound-event-sink"],
                    }
                    for sink in sinks
                }
            }
        }
    )
    by_id = {sink.integration_id: sink for sink in sinks}
    return IntegrationRuntime(
        configuration,
        adapters=(
            AdapterRegistration(
                provider="fixture-sink",
                transport=TransportKind.WEBHOOK,
                capability=CapabilitySurface.OUTBOUND_EVENT_SINK,
                required_transport_operations=frozenset(),
                factory=lambda binding, transport: by_id[binding.integration_id],
            ),
        ),
        transports={TransportKind.WEBHOOK: lambda binding: _FixtureTransport()},
    )


def test_registered_event_envelopes_have_deterministic_ids_and_minimized_data():
    interrupted = _state(status=RunStatus.AWAITING_HUMAN, phase="pause")
    interrupted = interrupted.model_copy(
        update={
            "interrupt_log": [
                InterruptLogEntry(
                    trigger="budget-exceeded",
                    timestamp=NOW,
                    context={"error": "contains no publishable transcript"},
                )
            ]
        }
    )

    event = events_for_state(interrupted)[0]

    assert event.event_type is OutboundEventType.HUMAN_INTERRUPT
    assert event.event_id == events_for_state(interrupted)[0].event_id
    assert event.occurred_at == NOW
    assert event.provenance.run_id == interrupted.run_id
    assert event.provenance.work_item_id == interrupted.ticket_id
    assert event.data.model_dump() == {
        "kind": "human_interrupt",
        "interrupt_id": "run-btn-73:interrupt:0",
        "trigger": "budget-exceeded",
        "phase": "pause",
    }
    assert "context" not in event.model_dump_json()

    assert events_for_state(_state(status=RunStatus.DONE), clock=lambda: NOW)[0].event_type is (
        OutboundEventType.RUN_COMPLETED
    )
    assert events_for_state(
        _state(status=RunStatus.FAILED_INFRA, phase="architect"), clock=lambda: NOW
    )[0].event_type is OutboundEventType.RUN_FAILED


def test_event_schema_rejects_mismatched_data_and_naive_occurrence_time():
    provenance = EventRunProvenance(run_id="run-73")
    with pytest.raises(ValueError, match="requires HumanInterruptData"):
        OutboundEvent(
            event_id=UUID("30000000-0000-4000-8000-000000000073"),
            event_type=OutboundEventType.HUMAN_INTERRUPT,
            occurred_at=NOW,
            provenance=provenance,
            data=RunCompletedData(),
        )
    with pytest.raises(ValueError, match="timezone"):
        OutboundEvent(
            event_id=UUID("30000000-0000-4000-8000-000000000073"),
            event_type=OutboundEventType.RUN_COMPLETED,
            occurred_at=NOW.replace(tzinfo=None),
            provenance=provenance,
            data=RunCompletedData(),
        )


def test_publisher_fans_out_with_per_sink_idempotency_and_no_payload_persistence():
    state = _state(status=RunStatus.DONE)
    first = _Sink("sink-primary", "primary")
    second = _Sink("sink-secondary", "secondary")
    event = events_for_state(state, clock=lambda: NOW)[0]
    publisher = OutboundEventPublisher(state, clock=lambda: NOW)
    persisted: list[str] = []

    receipts = publisher.publish(
        (event,), sinks=(first, second), persist=lambda: persisted.append("saved")
    )

    assert len(receipts) == 2
    assert [sink.events[0][0] for sink in (first, second)] == [event, event]
    assert first.events[0][1] != second.events[0][1]
    assert len(state.side_effect_ledger.operations) == 2
    assert {record.capability for record in state.side_effect_ledger.operations} == {
        "outbound-event-sink"
    }
    assert all(record.operation == "event.publish" for record in state.side_effect_ledger.operations)
    assert len(persisted) == 4  # write-ahead intent and outcome per destination

    replay = publisher.publish((event,), sinks=(first,), persist=lambda: None)
    assert replay[0].replayed is True
    assert len(first.events) == 1


def test_application_publishes_after_state_is_durable_and_sink_failure_is_non_authorizing(tmp_path):
    successful = _Sink("sink-success", "success")
    failed = _Sink(
        "sink-timeout", "timeout", failure=IntegrationTimeout("response lost")
    )
    runtime = _runtime(successful, failed)
    # This focused fixture is not registered in a project catalog, so it uses
    # the pre-catalog compatible shape while still exercising bounded Run data.
    initial = _state(status=RunStatus.NOT_STARTED, phase="architect").model_copy(
        update={"project_id": None}
    )

    result = start_run(
        StartRun(initial_state=initial, config=BattalionConfig(base_dir=str(tmp_path))),
        state_dir=tmp_path / "state",
        integration_runtime=runtime,
        _execute=lambda **kwargs: kwargs["initial_state"].model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        ),
    )

    assert result.state.status is RunStatus.DONE
    assert [event.event_type for event, _ in successful.events] == [
        OutboundEventType.RUN_COMPLETED
    ]
    timeout_record = next(
        record
        for record in result.state.side_effect_ledger.operations
        if record.integration_id == "sink-timeout"
    )
    assert timeout_record.status.value == "ambiguous"
    persisted = RunState.model_validate_json(result.state_path.read_text(encoding="utf-8"))
    assert persisted.side_effect_ledger == result.state.side_effect_ledger


def test_runtime_exposes_only_configured_and_policy_permitted_event_sinks():
    first = _Sink("sink-primary", "primary")
    second = _Sink("sink-secondary", "secondary")

    runtime = _runtime(first, second)

    resolved = runtime.outbound_event_sinks()
    assert {sink.integration_id for sink in resolved} == {"sink-primary", "sink-secondary"}
    assert all(not hasattr(sink, "state") for sink in resolved)
