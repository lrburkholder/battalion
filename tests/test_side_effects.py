"""BTN-70: durable side-effect evidence and idempotent delivery."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from battalion.integrations.configuration import CapabilitySurface, TransportKind
from battalion.integrations.effects import (
    AlreadyResolved,
    DeliveryReceipt,
    DuplicateSideEffectKey,
    ProviderEvidence,
    ReconciliationRequired,
    SideEffectCoordinator,
    UnknownSideEffectOperation,
    request_digest,
)
from battalion.integrations.runtime import (
    IntegrationCancelled,
    IntegrationError,
    IntegrationMalformedResponse,
    IntegrationTimeout,
)
from battalion.state.models import (
    RunState,
    SideEffectAttempt,
    SideEffectLedger,
    SideEffectOperation,
    SideEffectOutcome,
    SideEffectStatus,
)
from battalion.state.persistence import load_state, save_state
from support.state import make_run_state

NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


class _Rejection(IntegrationError):
    """An adapter-typed confirmed external rejection."""

DESCRIPTOR = {
    "capability": CapabilitySurface.NOTIFICATION,
    "integration_id": "integrations/slack-workspace",
    "integration_name": "slack",
    "provider": "slack-like",
    "transport": TransportKind.WEBHOOK,
    "operation": "notification.send",
}


class TickingClock:
    """Deterministic clock advancing one minute per call."""

    def __init__(self, start: datetime = NOW) -> None:
        self.value = start

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(minutes=1)
        return current


class FakeDelivery:
    """Records every send; each queued item is a value or an exception."""

    def __init__(self, *outcomes: object) -> None:
        self.calls: list[str] = []
        self.outcomes = list(outcomes)

    def __call__(self, operation_id: str) -> object:
        self.calls.append(operation_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingPersist:
    """Persists RunState to disk like the application boundary would."""

    def __init__(self, state: RunState, path) -> None:
        self.state = state
        self.path = path
        self.events: list[str] = []

    def __call__(self) -> None:
        save_state(self.state, self.path)
        self.events.append("save")


def make_coordinator(state=None, tmp_path=None):
    state = state or make_run_state(ticket_id="BTN-70")
    persist = RecordingPersist(state, tmp_path / "state.json") if tmp_path else None
    coordinator = SideEffectCoordinator(state, clock=TickingClock())
    return state, coordinator, persist


def persist_callback(persist):
    return (lambda: persist()) if persist is not None else (lambda: None)


# --- State-contract validation ------------------------------------------------


def test_fresh_run_state_has_empty_ledger():
    state = make_run_state()
    assert state.side_effect_ledger == SideEffectLedger()
    assert state.side_effect_ledger.schema_version == "1.0"


def test_legacy_state_without_ledger_field_still_loads(tmp_path):
    legacy = make_run_state()
    payload = legacy.model_dump(mode="json")
    payload.pop("side_effect_ledger")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_state(path)
    assert loaded.side_effect_ledger == SideEffectLedger()


def test_attempt_outcome_evidence_rules():
    with pytest.raises(ValueError, match="failure category"):
        SideEffectAttempt(
            attempt_number=1,
            started_at=NOW,
            ended_at=NOW,
            outcome=SideEffectOutcome.SUCCEEDED,
            failure_category="timeout",
        )
    with pytest.raises(ValueError, match="failure category"):
        SideEffectAttempt(
            attempt_number=1, started_at=NOW, ended_at=NOW, outcome=SideEffectOutcome.FAILED
        )
    with pytest.raises(ValueError, match="end before"):
        SideEffectAttempt(
            attempt_number=1,
            started_at=NOW,
            ended_at=NOW - timedelta(minutes=1),
            outcome=SideEffectOutcome.SUCCEEDED,
        )


def test_naive_timestamps_rejected():
    naive = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone"):
        SideEffectOperation(
            operation_id=f"op-{uuid4()}",
            run_id="run-1",
            capability=CapabilitySurface.NOTIFICATION.value,
            integration_id="i",
            integration_name="n",
        provider="p",
        transport=TransportKind.WEBHOOK.value,
        operation="notification.send",
        dedupe_key="event-naive-time",
        created_at=naive,
        )
    succeeded = _succeeded_operation()
    payload = succeeded.model_dump(mode="python")
    payload["attempts"][0]["started_at"] = naive
    with pytest.raises(ValueError, match="timezone"):
        SideEffectOperation.model_validate(payload)


def test_operation_status_must_match_attempts():
    succeeded = _succeeded_operation()

    pending = succeeded.model_dump()
    pending["status"] = SideEffectStatus.PENDING
    with pytest.raises(ValueError, match="pending operations"):
        SideEffectOperation.model_validate(pending)

    ambiguous = succeeded.model_dump()
    ambiguous["status"] = SideEffectStatus.AMBIGUOUS
    with pytest.raises(ValueError, match="matching final attempt"):
        SideEffectOperation.model_validate(ambiguous)

    reversed_attempts = _succeeded_operation(attempt_numbers=(1, 2)).model_dump()
    reversed_attempts["attempts"] = list(reversed(reversed_attempts["attempts"]))
    with pytest.raises(ValueError, match="attempt numbers"):
        SideEffectOperation.model_validate(reversed_attempts)
def test_terminal_operation_requires_attempt_or_resolution():
    fields = _operation_fields()
    with pytest.raises(ValueError, match="attempt or reconciliation"):
        SideEffectOperation(**fields, status=SideEffectStatus.SUCCEEDED)


def test_reconciliation_requires_timestamp_and_detail():
    resolved = SideEffectOperation(
        **_operation_fields(),
        status=SideEffectStatus.FAILED,
        reconciled_at=NOW,
        reconciliation_detail="Provider confirms nothing was recorded",
    )
    assert resolved.reconciled_at == NOW
    unresolved = _succeeded_operation()
    payload = unresolved.model_dump()
    payload["reconciled_at"] = NOW
    with pytest.raises(ValueError, match="timestamp and detail"):
        SideEffectOperation.model_validate(payload)


def test_ledger_identity_is_unique():
    first = _succeeded_operation()
    second = _succeeded_operation(operation_id=first.operation_id)
    with pytest.raises(ValueError, match="operation IDs must be unique"):
        SideEffectLedger(operations=[first, second])
    third = _succeeded_operation(dedupe_key="event-1")
    fourth = _succeeded_operation(dedupe_key=third.dedupe_key)
    with pytest.raises(ValueError, match="dedupe keys must be unique"):
        SideEffectLedger(operations=[third, fourth])


# --- Coordinator delivery semantics -------------------------------------------


def test_successful_delivery_records_complete_durable_evidence(tmp_path):
    state, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    actor = uuid4()
    delivery = FakeDelivery(ProviderEvidence(value=True, provider_idempotency_used=True))

    receipt = coordinator.execute(
        **DESCRIPTOR,
        actor_id=actor,
        dedupe_key="interrupt-1",
        deliver=delivery,
        persist=persist_callback(persist),
        request_digest_value=request_digest({"text": "Run paused"}),
    )

    assert isinstance(receipt, DeliveryReceipt)
    assert receipt.replayed is False
    record = coordinator.get(receipt.operation_id)
    assert record.status is SideEffectStatus.SUCCEEDED
    assert record.run_id == state.run_id
    assert record.actor_id == actor
    assert record.capability == CapabilitySurface.NOTIFICATION.value
    assert record.integration_id == "integrations/slack-workspace"
    assert record.operation == "notification.send"
    assert record.created_at == NOW
    attempt = record.attempts[0]
    assert attempt.attempt_number == 1
    assert attempt.outcome is SideEffectOutcome.SUCCEEDED
    assert attempt.started_at < attempt.ended_at
    assert attempt.provider_idempotency_used is True
    assert attempt.request_digest == request_digest({"text": "Run paused"})
    # Write-ahead intent: one save before the send, one after.
    assert persist.events == ["save", "save"]
    assert delivery.calls == [record.operation_id]


def test_duplicate_processing_after_success_replays_without_sending(tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    delivery = FakeDelivery(True)

    first = coordinator.execute(
        **DESCRIPTOR, dedupe_key="event-42", deliver=delivery, persist=persist_callback(persist)
    )
    replay = coordinator.execute(
        **DESCRIPTOR, dedupe_key="event-42", deliver=delivery, persist=persist_callback(persist)
    )

    assert delivery.calls == [first.operation_id]
    assert replay.replayed is True
    assert replay.operation_id == first.operation_id
    assert replay.status is SideEffectStatus.SUCCEEDED


def test_delivery_requires_a_stable_dedupe_key_before_recording_or_sending(tmp_path):
    state, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    delivery = FakeDelivery(True)

    with pytest.raises(TypeError, match="dedupe_key"):
        coordinator.execute(
            **DESCRIPTOR, deliver=delivery, persist=persist_callback(persist)
        )

    assert delivery.calls == []
    assert state.side_effect_ledger.operations == []


def test_confirmed_failure_can_retry_under_same_logical_identity(tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    rejection = _Rejection("provider rejected the webhook")

    with pytest.raises(IntegrationError):
        coordinator.execute(
            **DESCRIPTOR,
            dedupe_key="notify-1",
            deliver=FakeDelivery(rejection),
            persist=persist_callback(persist),
        )
    record = coordinator.unresolved()
    assert record == []
    failed = coordinator.find_by_key("notify-1")
    assert failed.status is SideEffectStatus.FAILED
    assert failed.attempts[0].outcome is SideEffectOutcome.FAILED

    retry_delivery = FakeDelivery(True)
    receipt = coordinator.execute(
        **DESCRIPTOR,
        dedupe_key="notify-1",
        deliver=retry_delivery,
        persist=persist_callback(persist),
    )
    assert retry_delivery.calls == [failed.operation_id]
    assert receipt.attempt_number == 2
    reloaded = coordinator.find_by_key("notify-1")
    assert [attempt.attempt_number for attempt in reloaded.attempts] == [1, 2]
    assert reloaded.status is SideEffectStatus.SUCCEEDED


def test_timeout_after_remote_success_is_ambiguous_and_blocks_redelivery(tmp_path):
    path = tmp_path / "state.json"
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    delivery = FakeDelivery(IntegrationTimeout("response never arrived"))

    with pytest.raises(IntegrationTimeout):
        coordinator.execute(
            **DESCRIPTOR, dedupe_key="evt-7", deliver=delivery, persist=persist_callback(persist)
        )
    ambiguous = coordinator.find_by_key("evt-7")
    assert ambiguous.status is SideEffectStatus.AMBIGUOUS
    assert ambiguous.attempts[0].outcome is SideEffectOutcome.AMBIGUOUS
    assert ambiguous.attempts[0].failure_category == "timeout"
    assert coordinator.unresolved() == [ambiguous]

    resumed_state = load_state(path)
    resumed = SideEffectCoordinator(resumed_state, clock=TickingClock())
    with pytest.raises(ReconciliationRequired):
        resumed.execute(
            **DESCRIPTOR, dedupe_key="evt-7", deliver=FakeDelivery(True), persist=lambda: None
        )

    resumed_persist = RecordingPersist(resumed_state, path)
    reconciled = resumed.reconcile(
        ambiguous.operation_id,
        outcome="succeeded",
        basis="Provider status endpoint confirms message id was accepted",
        provider_reference="msg_01H",
        persist=resumed_persist,
    )
    assert reconciled.status is SideEffectStatus.SUCCEEDED

    replay = resumed.execute(
        **DESCRIPTOR, dedupe_key="evt-7", deliver=FakeDelivery(True), persist=resumed_persist
    )
    assert replay.replayed is True
    assert len(delivery.calls) == 1

    persisted_final = load_state(path).side_effect_ledger.operations[0]
    assert persisted_final.status is SideEffectStatus.SUCCEEDED
    assert persisted_final.attempts[-1].provider_reference == "msg_01H"


def test_crash_before_response_requires_reconciliation_on_resume(tmp_path):
    path = tmp_path / "state.json"
    crashed = make_run_state(run_id="crash-run")
    writer = SideEffectCoordinator(crashed, clock=TickingClock())
    opened = writer.open_operation(**DESCRIPTOR, dedupe_key="evt-crash")
    save_state(crashed, path)
    # Simulated crash: no attempt ever recorded for the persisted intent.

    resumed_state = load_state(path)
    resumed = SideEffectCoordinator(resumed_state, clock=TickingClock())
    assert [op.operation_id for op in resumed.unresolved()] == [opened.operation_id]
    with pytest.raises(ReconciliationRequired):
        resumed.execute(
            **DESCRIPTOR, dedupe_key="evt-crash", deliver=FakeDelivery(True), persist=lambda: None
        )

    resumed.reconcile(
        opened.operation_id,
        outcome="failed",
        basis="Local delivery log shows the request never left this machine",
        persist=lambda: None,
    )
    save_state(resumed_state, path)

    retried_delivery = FakeDelivery(True)
    receipt = SideEffectCoordinator(load_state(path), clock=TickingClock()).execute(
        **DESCRIPTOR, dedupe_key="evt-crash", deliver=retried_delivery, persist=lambda: None
    )
    assert retried_delivery.calls == [opened.operation_id]
    assert receipt.attempt_number == 1
    assert receipt.status is SideEffectStatus.SUCCEEDED


def test_resume_preserves_ledger_identity_and_gates(tmp_path):
    path = tmp_path / "state.json"
    state = make_run_state()
    coordinator = SideEffectCoordinator(state, clock=TickingClock())
    delivery = FakeDelivery(IntegrationCancelled("cancelled mid-flight"))
    with pytest.raises(IntegrationCancelled):
        coordinator.execute(
            **DESCRIPTOR, dedupe_key="evt-r", deliver=delivery, persist=RecordingPersist(state, path)
        )

    loaded = load_state(path)
    assert loaded.side_effect_ledger.operations[0].dedupe_key == "evt-r"
    resumed = SideEffectCoordinator(loaded, clock=TickingClock())
    assert resumed.get(coordinator.find_by_key("evt-r").operation_id).status is (
        SideEffectStatus.AMBIGUOUS
    )
    with pytest.raises(ReconciliationRequired):
        resumed.execute(
            **DESCRIPTOR, dedupe_key="evt-r", deliver=FakeDelivery(True), persist=lambda: None
        )


def test_duplicate_processing_of_unresolved_event_converges(tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    delivery = FakeDelivery(IntegrationTimeout("no response"), IntegrationTimeout("again"))

    with pytest.raises(IntegrationTimeout):
        coordinator.execute(
            **DESCRIPTOR, dedupe_key="dup", deliver=delivery, persist=persist_callback(persist)
        )
    with pytest.raises(ReconciliationRequired):
        coordinator.execute(
            **DESCRIPTOR, dedupe_key="dup", deliver=delivery, persist=persist_callback(persist)
        )
    assert len(delivery.calls) == 1


def test_conflicting_dedupe_key_descriptor_is_rejected(tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    coordinator.execute(
        **DESCRIPTOR, dedupe_key="key-1", deliver=FakeDelivery(True), persist=persist_callback(persist)
    )
    conflicting = dict(DESCRIPTOR, operation="work.comment")
    with pytest.raises(DuplicateSideEffectKey):
        coordinator.execute(
            **conflicting, dedupe_key="key-1", deliver=FakeDelivery(True),
            persist=persist_callback(persist),
        )


def test_write_ahead_persist_precedes_first_send():
    order: list[str] = []

    def persist() -> None:
        order.append("persist")

    def deliver(operation_id: str) -> bool:
        order.append(f"deliver:{operation_id}")
        return True

    state = make_run_state()
    coordinator = SideEffectCoordinator(state, clock=TickingClock())
    coordinator.execute(
        **DESCRIPTOR, dedupe_key="write-ahead-1", deliver=deliver, persist=persist
    )
    assert order[0] == "persist"
    assert order[1].startswith("deliver:op-")


def test_unexpected_error_leaves_pending_intent_for_reconciliation(tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)

    class Unexpected(Exception):
        pass

    with pytest.raises(Unexpected):
        coordinator.execute(
            **DESCRIPTOR,
            dedupe_key="bug",
            deliver=FakeDelivery(Unexpected("caller bug")),
            persist=persist_callback(persist),
        )
    pending = coordinator.find_by_key("bug")
    assert pending.status is SideEffectStatus.PENDING
    assert pending.attempts == []
    assert coordinator.unresolved() == [pending]


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (IntegrationTimeout("t"), SideEffectOutcome.AMBIGUOUS),
        (IntegrationCancelled("c"), SideEffectOutcome.AMBIGUOUS),
        (IntegrationMalformedResponse("m"), SideEffectOutcome.AMBIGUOUS),
        (_Rejection("r"), SideEffectOutcome.FAILED),
    ],
)
def test_failure_classification(error, category, tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    with pytest.raises(IntegrationError):
        coordinator.execute(
            **DESCRIPTOR, dedupe_key=f"k-{category.value}",
            deliver=FakeDelivery(error), persist=persist_callback(persist),
        )
    record = coordinator.find_by_key(f"k-{category.value}")
    assert record.attempts[0].outcome is category


def test_already_resolved_and_unknown_operations(tmp_path):
    _, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    receipt = coordinator.execute(
        **DESCRIPTOR, dedupe_key="done", deliver=FakeDelivery(True), persist=persist_callback(persist)
    )
    with pytest.raises(AlreadyResolved):
        coordinator.reconcile(
            receipt.operation_id, outcome="failed", basis="late probe", persist=lambda: None
        )
    with pytest.raises(UnknownSideEffectOperation):
        coordinator.get("op-00000000-0000-0000-0000-000000000000")


def test_serialized_ledger_excludes_payload_contents(tmp_path):
    state, coordinator, persist = make_coordinator(tmp_path=tmp_path)
    secret_text = "super-secret-webhook-token-abc123"
    coordinator.execute(
        **DESCRIPTOR,
        dedupe_key="secret-check",
        deliver=FakeDelivery(ProviderEvidence(value=True)),
        persist=persist_callback(persist),
        request_digest_value=request_digest({"text": secret_text}),
    )
    save_state(state, tmp_path / "final.json")
    serialized = (tmp_path / "final.json").read_text(encoding="utf-8")
    assert secret_text not in serialized
    assert request_digest({"text": secret_text}) in serialized


def test_request_digest_is_content_addressed():
    assert request_digest({"b": 1, "a": 2}) == request_digest({"a": 2, "b": 1})
    assert request_digest({"a": 1}) != request_digest({"a": 2})


# --- Helpers ------------------------------------------------------------------


def _operation_fields(**overrides):
    fields = dict(
        operation_id=f"op-{uuid4()}",
        run_id="run-x",
        capability=CapabilitySurface.NOTIFICATION.value,
        integration_id="integrations/slack-workspace",
        integration_name="slack",
        provider="slack-like",
        transport=TransportKind.WEBHOOK.value,
        operation="notification.send",
        dedupe_key=f"event-{uuid4()}",
        created_at=NOW,
    )
    fields.update(overrides)
    return fields


def _succeeded_operation(*, attempt_numbers=(1,), operation_id=None, dedupe_key=None):
    attempts = [
        SideEffectAttempt(
            attempt_number=number,
            started_at=NOW + timedelta(minutes=number),
            ended_at=NOW + timedelta(minutes=number, seconds=30),
            outcome=SideEffectOutcome.SUCCEEDED,
        )
        for number in attempt_numbers
    ]
    fields = _operation_fields(operation_id=operation_id or f"op-{uuid4()}")
    if dedupe_key is not None:
        fields["dedupe_key"] = dedupe_key
    return SideEffectOperation(
        **fields,
        status=SideEffectStatus.SUCCEEDED,
        attempts=attempts,
    )
