"""Framework-neutral live observation contract for active runs.

Observation is deliberately one-way: publishers describe graph progress while
authoritative state continues to live in ``RunState`` persistence.  Transient
events may be dropped; reconnecting clients recover from a durable snapshot and
then consume only events newer than the snapshot barrier.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ObservationCategory(str, Enum):
    """How a client should treat an observation during recovery."""

    DURABLE = "durable"
    TRANSIENT = "transient"
    ACTION_REQUIRED = "action-required"


class ObservationKind(str, Enum):
    STATE_CHECKPOINT = "state-checkpoint"
    NODE_STARTED = "node-started"
    NODE_FINISHED = "node-finished"
    NODE_FAILED = "node-failed"
    TOKEN = "token"
    REASONING = "reasoning"
    INTERRUPT = "interrupt"


_CATEGORY_BY_KIND = {
    ObservationKind.STATE_CHECKPOINT: ObservationCategory.DURABLE,
    ObservationKind.NODE_STARTED: ObservationCategory.TRANSIENT,
    ObservationKind.NODE_FINISHED: ObservationCategory.TRANSIENT,
    ObservationKind.NODE_FAILED: ObservationCategory.TRANSIENT,
    ObservationKind.TOKEN: ObservationCategory.TRANSIENT,
    ObservationKind.REASONING: ObservationCategory.TRANSIENT,
    ObservationKind.INTERRUPT: ObservationCategory.ACTION_REQUIRED,
}


class ObservationCursor(BaseModel):
    """Position in one run operation's independently ordered event stream."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    stream_id: UUID
    sequence: int = Field(ge=0)


class ObservationEvent(BaseModel):
    """One immutable, deduplicable live observation."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    run_id: str = Field(min_length=1)
    stream_id: UUID
    sequence: int = Field(ge=1)
    occurred_at: datetime
    category: ObservationCategory
    kind: ObservationKind
    node: str | None = Field(default=None, min_length=1)
    attempt_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> "ObservationEvent":
        if self.event_id != uuid5(self.stream_id, str(self.sequence)):
            raise ValueError("event_id must be derived from stream_id and sequence")
        expected = _CATEGORY_BY_KIND[self.kind]
        if self.category != expected:
            raise ValueError(f"{self.kind.value} events must be {expected.value}")
        node_kinds = {
            ObservationKind.NODE_STARTED,
            ObservationKind.NODE_FINISHED,
            ObservationKind.NODE_FAILED,
            ObservationKind.TOKEN,
            ObservationKind.REASONING,
        }
        if self.kind in node_kinds and (self.node is None or self.attempt_id is None):
            raise ValueError(f"{self.kind.value} events require node and attempt_id")
        return self

    @property
    def cursor(self) -> ObservationCursor:
        return ObservationCursor(
            run_id=self.run_id,
            stream_id=self.stream_id,
            sequence=self.sequence,
        )


ObservationCallback = Callable[[ObservationEvent], None]


class ObservationSource(Protocol):
    """Minimum transport capability required to establish a reconnect barrier."""

    def barrier(self, run_id: str, stream_id: UUID) -> ObservationCursor: ...


class RunObservationPublisher:
    """Adapt one graph operation's raw callbacks to typed observations."""

    def __init__(
        self,
        run_id: str,
        emit: ObservationCallback,
        *,
        stream_id: UUID | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        self.run_id = run_id
        self.stream_id = stream_id or uuid4()
        self._emit = emit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sequence = 0
        self._node: str | None = None
        self._attempt_id: UUID | None = None

    @property
    def cursor(self) -> ObservationCursor:
        return ObservationCursor(
            run_id=self.run_id,
            stream_id=self.stream_id,
            sequence=self._sequence,
        )

    def handle_node_event(self, raw: dict[str, Any]) -> None:
        event_type = raw.get("type")
        node = raw.get("node")
        if event_type == "node_start":
            if not isinstance(node, str) or not node:
                raise ValueError("node_start requires a non-empty node")
            self._node = node
            self._attempt_id = uuid4()
            self._publish(ObservationKind.NODE_STARTED, node=node, payload=raw)
        elif event_type == "node_end":
            self._require_attempt(event_type, node)
            self._publish(ObservationKind.NODE_FINISHED, node=node, payload=raw)
            self._node = None
            self._attempt_id = None
        elif event_type == "node_error":
            self._require_attempt(event_type, node)
            self._publish(ObservationKind.NODE_FAILED, node=node, payload=raw)
        elif event_type == "interrupt":
            self._publish(ObservationKind.INTERRUPT, node=node, payload=raw)
        else:
            raise ValueError(f"Unsupported node observation type: {event_type!r}")

    def handle_token(self, raw: dict[str, Any]) -> None:
        event_type = raw.get("type")
        if event_type not in {"token", "reasoning"}:
            raise ValueError(f"Unsupported token observation type: {event_type!r}")
        self._require_attempt(event_type, self._node)
        kind = (
            ObservationKind.TOKEN
            if event_type == "token"
            else ObservationKind.REASONING
        )
        self._publish(kind, node=self._node, payload=raw)

    def handle_checkpoint(self, state: Any) -> None:
        """Publish a pointer to state that has already been durably saved."""
        self._publish(
            ObservationKind.STATE_CHECKPOINT,
            payload={
                "state_version": state.schema_version,
                "status": state.status.value,
                "phase": state.phase,
            },
        )

    def _require_attempt(self, event_type: str, node: object) -> None:
        if node != self._node or self._attempt_id is None:
            raise ValueError(f"{event_type} does not match an active node attempt")

    def _publish(
        self,
        kind: ObservationKind,
        *,
        node: str | None = None,
        payload: dict[str, Any],
    ) -> None:
        self._sequence += 1
        event = ObservationEvent(
            event_id=uuid5(self.stream_id, str(self._sequence)),
            run_id=self.run_id,
            stream_id=self.stream_id,
            sequence=self._sequence,
            occurred_at=self._clock(),
            category=_CATEGORY_BY_KIND[kind],
            kind=kind,
            node=node,
            attempt_id=self._attempt_id,
            payload=dict(payload),
        )
        self._emit(event)


class ObservationBuffer:
    """Bounded reference transport used by adapters and deterministic tests.

    Production presentation frameworks may implement another transport with the
    same cursor semantics.  Buffer eviction is expected and safe because live
    events are never the recovery authority.
    """

    def __init__(self, max_events_per_stream: int = 1000) -> None:
        if max_events_per_stream < 1:
            raise ValueError("max_events_per_stream must be positive")
        self._max_events = max_events_per_stream
        self._events: dict[tuple[str, UUID], deque[ObservationEvent]] = defaultdict(
            lambda: deque(maxlen=self._max_events)
        )
        self._latest: dict[tuple[str, UUID], int] = {}

    def publish(self, event: ObservationEvent | dict[str, Any]) -> None:
        validated = ObservationEvent.model_validate(event)
        key = (validated.run_id, validated.stream_id)
        latest = self._latest.get(key, 0)
        if validated.sequence <= latest:
            retained = next(
                (
                    item
                    for item in self._events[key]
                    if item.event_id == validated.event_id
                ),
                None,
            )
            if retained is None:
                return
            if retained == validated:
                return
            raise ValueError("duplicate event_id has conflicting content")
        self._events[key].append(validated)
        self._latest[key] = validated.sequence

    def barrier(self, run_id: str, stream_id: UUID) -> ObservationCursor:
        return ObservationCursor(
            run_id=run_id,
            stream_id=stream_id,
            sequence=self._latest.get((run_id, stream_id), 0),
        )

    def after(self, cursor: ObservationCursor) -> tuple[ObservationEvent, ...]:
        return tuple(
            event
            for event in self._events[(cursor.run_id, cursor.stream_id)]
            if event.sequence > cursor.sequence
        )


def ordered_unique_events(
    events: Iterable[ObservationEvent | dict[str, Any]],
) -> tuple[ObservationEvent, ...]:
    """Validate, deduplicate, and deterministically order a delivered batch."""
    unique: dict[UUID, ObservationEvent] = {}
    for event in events:
        validated = ObservationEvent.model_validate(event)
        previous = unique.get(validated.event_id)
        if previous is not None and previous != validated:
            raise ValueError("duplicate event_id has conflicting content")
        unique[validated.event_id] = validated
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.run_id, str(item.stream_id), item.sequence),
        )
    )
