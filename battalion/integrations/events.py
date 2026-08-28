"""Versioned, minimized outbound domain events (BTN-73).

The application creates these envelopes only after a durable Run transition.
Provider adapters receive an envelope and Battalion's stable side-effect
operation ID, never a ``RunState``, graph handle, Actor authority, prompt, or
arbitrary execution record.  Delivery evidence and replay safety remain in
``SideEffectCoordinator`` (BTN-70).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from battalion.integrations.configuration import CapabilitySurface, TransportKind
from battalion.integrations.effects import DeliveryReceipt, SideEffectCoordinator, request_digest
from battalion.state.models import RunState, RunStatus


class OutboundEventType(str, Enum):
    """The explicitly registered external event types in schema version 1.0."""

    HUMAN_INTERRUPT = "human_interrupt"
    RUN_FAILED = "run_failed"
    RUN_COMPLETED = "run_completed"


class EventRunProvenance(BaseModel):
    """The bounded, non-authorizing Run/project identity carried by an event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=200)
    run_alias: str | None = Field(default=None, max_length=200)
    project_id: UUID | None = None


class HumanInterruptData(BaseModel):
    """Safe interrupt summary; arbitrary trigger context is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["human_interrupt"] = "human_interrupt"
    interrupt_id: str = Field(min_length=1, max_length=300)
    trigger: str = Field(min_length=1, max_length=100)


class RunFailedData(BaseModel):
    """A bounded state classification, rather than raw exception diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["run_failed"] = "run_failed"
    status: Literal["blocked", "failed-infra"]
    phase: str = Field(min_length=1, max_length=200)


class RunCompletedData(BaseModel):
    """Completion carries no transcript, artifact, prompt, or model evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["run_completed"] = "run_completed"
    status: Literal["done"] = "done"


OutboundEventData = HumanInterruptData | RunFailedData | RunCompletedData


class OutboundEvent(BaseModel):
    """A provider-neutral, one-way external event envelope.

    Schema versions are additive-compatible only within a major version. New
    required fields, renamed fields, changed meanings, or removed fields need
    a new schema version and a separately registered event type/version pair.
    Consumers must ignore unknown optional fields and reject unknown major
    versions rather than guessing at their meaning.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    event_type: OutboundEventType
    schema_version: Literal["1.0"] = "1.0"
    occurred_at: datetime
    provenance: EventRunProvenance
    data: OutboundEventData

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event occurrence time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_registered_data_shape(self) -> "OutboundEvent":
        expected = {
            OutboundEventType.HUMAN_INTERRUPT: HumanInterruptData,
            OutboundEventType.RUN_FAILED: RunFailedData,
            OutboundEventType.RUN_COMPLETED: RunCompletedData,
        }[self.event_type]
        if not isinstance(self.data, expected):
            raise ValueError(
                f"event type {self.event_type.value!r} requires "
                f"{expected.__name__} data"
            )
        return self


class ConfiguredOutboundEventSink(Protocol):
    """The application-facing sink shape, with no authority-bearing values."""

    @property
    def integration_id(self) -> str: ...

    @property
    def integration_name(self) -> str: ...

    @property
    def provider(self) -> str: ...

    @property
    def transport(self) -> TransportKind: ...

    def accepts(self, event: OutboundEvent) -> bool: ...

    def publish(self, event: OutboundEvent, *, idempotency_key: str) -> Any: ...


def events_for_state(
    state: RunState,
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[OutboundEvent, ...]:
    """Build every registered outbound event implied by one durable Run state.

    Event IDs are deterministic from durable Run facts. This gives retries and
    resume the same logical event identity without introducing a second event
    store. Events intentionally omit interrupt context, exception text,
    execution records, prompts, source content, and provider secrets.
    """

    now = clock or (lambda: datetime.now(timezone.utc))
    provenance = EventRunProvenance(
        run_id=state.run_id,
        run_alias=state.run_alias,
        project_id=state.project_id,
    )
    if state.status is RunStatus.AWAITING_HUMAN and state.interrupt_log:
        index = len(state.interrupt_log) - 1
        interrupt = state.interrupt_log[index]
        event_key = f"{state.run_id}:human_interrupt:{index}"
        return (
            OutboundEvent(
                event_id=_event_id(event_key),
                event_type=OutboundEventType.HUMAN_INTERRUPT,
                occurred_at=interrupt.timestamp,
                provenance=provenance,
                data=HumanInterruptData(
                    interrupt_id=f"{state.run_id}:interrupt:{index}",
                    trigger=interrupt.trigger,
                ),
            ),
        )
    if state.status in (RunStatus.BLOCKED, RunStatus.FAILED_INFRA):
        return (
            OutboundEvent(
                event_id=_event_id(f"{state.run_id}:run_failed"),
                event_type=OutboundEventType.RUN_FAILED,
                occurred_at=now(),
                provenance=provenance,
                data=RunFailedData(status=state.status.value, phase=state.phase),
            ),
        )
    if state.status is RunStatus.DONE:
        return (
            OutboundEvent(
                event_id=_event_id(f"{state.run_id}:run_completed"),
                event_type=OutboundEventType.RUN_COMPLETED,
                occurred_at=now(),
                provenance=provenance,
                data=RunCompletedData(),
            ),
        )
    return ()


class OutboundEventPublisher:
    """Publish registered events through configured sinks using BTN-70 evidence."""

    def __init__(self, state: RunState, *, clock: Callable[[], datetime] | None = None) -> None:
        self._coordinator = SideEffectCoordinator(state, clock=clock)

    def publish(
        self,
        events: Iterable[OutboundEvent],
        *,
        sinks: Iterable[ConfiguredOutboundEventSink],
        persist: Callable[[], None],
    ) -> tuple[DeliveryReceipt, ...]:
        """Deliver each explicit event to every configured machine sink.

        A provider receives one envelope and the ledger's operation ID as its
        idempotency key. The event ID is the dedupe key component, so a restart
        or duplicate transition replays safely for each destination.
        """

        receipts: list[DeliveryReceipt] = []
        destination_sinks = tuple(sinks)
        for event in events:
            digest = request_digest(event.model_dump(mode="json"))
            for sink in destination_sinks:
                # Event selection is evaluated before opening a durable
                # operation: intentionally unsubscribed event types are not
                # delivery attempts. Older in-process fixtures select all.
                accepts = getattr(sink, "accepts", lambda _: True)
                if not accepts(event):
                    continue
                receipts.append(
                    self._coordinator.execute(
                        capability=CapabilitySurface.OUTBOUND_EVENT_SINK,
                        integration_id=sink.integration_id,
                        integration_name=sink.integration_name,
                        provider=sink.provider,
                        transport=sink.transport,
                        operation="event.publish",
                        dedupe_key=f"outbound-event:{event.event_id}:{sink.integration_id}",
                        request_digest_value=digest,
                        persist=persist,
                        deliver=lambda operation_id, sink=sink, event=event: sink.publish(
                            event, idempotency_key=operation_id
                        ),
                    )
                )
        return tuple(receipts)


def _event_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://battalion.dev/outbound-events/1.0/{key}")
