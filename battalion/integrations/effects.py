"""Durable side-effect evidence and idempotent delivery (BTN-70).

The coordinator sits at the application boundary, beneath no framework and
above provider adapters (ADR-0025).  It owns the durable identity that makes
externally visible operations replay-safe across retries, resume, crashes,
and duplicate event processing:

1. A stable Battalion operation ID is minted and persisted *before* the first
   delivery attempt (write-ahead intent inside ``RunState.side_effect_ledger``).
2. The same operation ID is handed to the delivery callback so adapters can
   engage provider idempotency mechanisms where they exist; Battalion keeps
   its own identity regardless.
3. Every attempt is recorded with a typed outcome.  Timeouts, cancellations,
   transport failures, and malformed responses after submission are
   ``ambiguous`` per RFC-0006; confirmed rejections are ``failed``.
4. Unresolved operations (pending intents from a previous process or
   ambiguous attempts) refuse automatic redelivery until explicitly
   reconciled against provider status or idempotency evidence.

The coordinator performs no IO itself: callers inject ``persist`` so the
write-ahead ordering is mechanical rather than conventional.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import UUID, uuid4

from battalion.integrations.configuration import CapabilitySurface, TransportKind
from battalion.integrations.runtime import (
    IntegrationCancelled,
    IntegrationError,
    IntegrationMalformedResponse,
    IntegrationTimeout,
    IntegrationTransportFailure,
)
from battalion.state.models import (
    RunState,
    SideEffectAttempt,
    SideEffectOperation,
    SideEffectOutcome,
    SideEffectStatus,
)


class SideEffectDeliveryError(IntegrationError):
    """Base class for side-effect ledger failures."""


class UnknownSideEffectOperation(SideEffectDeliveryError):
    """No ledger operation matches the requested identity."""


class DuplicateSideEffectKey(SideEffectDeliveryError):
    """A dedupe key was reused for a different logical operation."""


class AlreadyResolved(SideEffectDeliveryError):
    """Reconciliation was requested for an already-resolved operation."""


class ReconciliationRequired(SideEffectDeliveryError):
    """An unresolved logical operation forbids automatic redelivery."""

    def __init__(self, operation: SideEffectOperation) -> None:
        self.operation = operation
        super().__init__(
            f"Operation {operation.operation_id!r} is {operation.status.value} and "
            "requires reconciliation against provider evidence before redelivery"
        )


_AMBIGUOUS_CATEGORIES: dict[type[IntegrationError], str] = {
    IntegrationTimeout: "timeout",
    IntegrationCancelled: "cancelled",
    IntegrationTransportFailure: "transport-failure",
    IntegrationMalformedResponse: "malformed-response",
}


def request_digest(payload: Any) -> str:
    """Content digest for audit evidence without retaining the payload."""

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderEvidence:
    """Optional provider-side facts returned by a delivery callback."""

    value: Any = None
    provider_idempotency_used: bool = False
    provider_reference: str | None = None


@dataclass(frozen=True)
class DeliveryReceipt:
    """Durable outcome of one coordinated logical operation."""

    operation_id: str
    status: SideEffectStatus
    attempt_number: int | None = None
    replayed: bool = False


class SideEffectCoordinator:
    """Owns logical-operation identity, evidence, and replay safety.

    The coordinator mutates ``state.side_effect_ledger`` in place; callers
    persist ``RunState`` through the injected callbacks exactly when told to,
    preserving the write-ahead guarantee.
    """

    def __init__(
        self,
        state: RunState,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_id = state.run_id
        self._ledger = state.side_effect_ledger
        self._session_opened: set[str] = set()
        self._clock: Callable[[], datetime] = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def _now(self) -> datetime:
        return self._clock()

    def open_operation(
        self,
        *,
        capability: CapabilitySurface,
        integration_id: str,
        integration_name: str,
        provider: str,
        transport: TransportKind,
        operation: str,
        actor_id: UUID | None = None,
        dedupe_key: str,
    ) -> SideEffectOperation:
        """Register durable intent for one logical external operation.

        ``dedupe_key`` is the caller's stable logical-operation or event
        identity. It is required so duplicate event processing, retry, and
        resume converge on one operation.
        """

        existing = self._find_by_key(dedupe_key)
        if existing is not None:
            self._ensure_descriptor(existing, capability, integration_id, operation)
            return existing

        record = SideEffectOperation(
            operation_id=f"op-{uuid4()}",
            dedupe_key=dedupe_key,
            run_id=self._run_id,
            actor_id=actor_id,
            capability=capability.value,
            integration_id=integration_id,
            integration_name=integration_name,
            provider=provider,
            transport=transport.value,
            operation=operation,
            created_at=self._now(),
        )
        self._ledger.operations.append(record)
        self._session_opened.add(record.operation_id)
        return record

    def execute(
        self,
        *,
        capability: CapabilitySurface,
        integration_id: str,
        integration_name: str,
        provider: str,
        transport: TransportKind,
        operation: str,
        deliver: Callable[[str], Any],
        persist: Callable[[], None],
        actor_id: UUID | None = None,
        dedupe_key: str,
        request_digest_value: str | None = None,
    ) -> DeliveryReceipt:
        """Deliver one logical operation at most once, durably.

        ``deliver`` receives Battalion's stable operation ID for use as the
        provider idempotency key where supported.  ``persist`` is invoked at
        the write-ahead points: after recording intent and after each attempt.
        """

        existing = self._find_by_key(dedupe_key)
        if existing is not None:
            self._ensure_descriptor(existing, capability, integration_id, operation)
            record = existing
        else:
            record = self.open_operation(
                capability=capability,
                integration_id=integration_id,
                integration_name=integration_name,
                provider=provider,
                transport=transport,
                operation=operation,
                actor_id=actor_id,
                dedupe_key=dedupe_key,
            )
            try:
                persist()
            except Exception:
                self._ledger.operations.remove(record)
                self._session_opened.discard(record.operation_id)
                raise

        if not self._gate(record):
            return DeliveryReceipt(
                operation_id=record.operation_id,
                status=record.status,
                replayed=True,
            )

        attempt_number = len(record.attempts) + 1
        started = self._now()
        try:
            result = deliver(record.operation_id)
        except IntegrationError as exc:
            outcome, category = _classify(exc)
            record.attempts.append(
                SideEffectAttempt(
                    attempt_number=attempt_number,
                    started_at=started,
                    ended_at=self._now(),
                    outcome=outcome,
                    failure_category=category,
                    detail=str(exc)[:2000],
                    request_digest=request_digest_value,
                )
            )
            record.status = (
                SideEffectStatus.AMBIGUOUS
                if outcome is SideEffectOutcome.AMBIGUOUS
                else SideEffectStatus.FAILED
            )
            persist()
            raise

        evidence = result if isinstance(result, ProviderEvidence) else ProviderEvidence(value=result)
        record.attempts.append(
            SideEffectAttempt(
                attempt_number=attempt_number,
                started_at=started,
                ended_at=self._now(),
                outcome=SideEffectOutcome.SUCCEEDED,
                provider_idempotency_used=evidence.provider_idempotency_used,
                provider_reference=evidence.provider_reference,
                request_digest=request_digest_value,
            )
        )
        record.status = SideEffectStatus.SUCCEEDED
        persist()
        return DeliveryReceipt(
            operation_id=record.operation_id,
            status=record.status,
            attempt_number=attempt_number,
        )

    def reconcile(
        self,
        operation_id: str,
        *,
        outcome: Literal["succeeded", "failed"],
        basis: str,
        persist: Callable[[], None],
        actor_id: UUID | None = None,
        provider_reference: str | None = None,
    ) -> DeliveryReceipt:
        """Resolve an unresolved operation from provider evidence.

        Reconciliation is the only path that may resolve a pending crash
        intent or an ambiguous attempt; it records who decided, when, and on
        what basis.
        """

        record = self.get(operation_id)
        if record.status in (SideEffectStatus.SUCCEEDED, SideEffectStatus.FAILED):
            raise AlreadyResolved(
                f"Operation {operation_id!r} is already {record.status.value}"
            )

        record.status = (
            SideEffectStatus.SUCCEEDED if outcome == "succeeded" else SideEffectStatus.FAILED
        )
        record.reconciled_at = self._now()
        record.reconciliation_detail = basis[:2000]
        if actor_id is not None and record.actor_id is None:
            record.actor_id = actor_id
        if provider_reference is not None:
            latest = record.attempts[-1] if record.attempts else None
            if latest is not None:
                latest.provider_reference = provider_reference
        persist()
        return DeliveryReceipt(operation_id=record.operation_id, status=record.status)

    def get(self, operation_id: str) -> SideEffectOperation:
        for record in self._ledger.operations:
            if record.operation_id == operation_id:
                return record
        raise UnknownSideEffectOperation(f"No ledger operation {operation_id!r}")

    def find_by_key(self, dedupe_key: str) -> SideEffectOperation | None:
        return self._find_by_key(dedupe_key)

    def unresolved(self) -> list[SideEffectOperation]:
        """Operations whose external effect is unproven or unrefuted."""

        return [
            record
            for record in self._ledger.operations
            if record.status in (SideEffectStatus.PENDING, SideEffectStatus.AMBIGUOUS)
        ]

    def _find_by_key(self, dedupe_key: str) -> SideEffectOperation | None:
        for record in self._ledger.operations:
            if record.dedupe_key == dedupe_key:
                return record
        return None

    @staticmethod
    def _ensure_descriptor(
        record: SideEffectOperation,
        capability: CapabilitySurface,
        integration_id: str,
        operation: str,
    ) -> None:
        if (
            record.capability != capability.value
            or record.integration_id != integration_id
            or record.operation != operation
        ):
            raise DuplicateSideEffectKey(
                f"Dedupe key {record.dedupe_key!r} already names "
                f"{record.capability}/{record.operation} on integration "
                f"{record.integration_id!r}"
            )

    def _gate(self, record: SideEffectOperation) -> bool:
        """Return ``True`` only when delivery may proceed now.

        Succeeded operations replay-skip; confirmed failures may retry with
        the same identity; anything unresolved (pending intents from another
        process, ambiguous attempts) demands reconciliation first.
        """

        if record.status is SideEffectStatus.FAILED:
            return True
        if record.status is SideEffectStatus.PENDING and (
            record.operation_id in self._session_opened
        ):
            return True
        if record.status is SideEffectStatus.SUCCEEDED:
            return False
        raise ReconciliationRequired(record)


def _classify(exc: IntegrationError) -> tuple[SideEffectOutcome, str]:
    for exc_type, category in _AMBIGUOUS_CATEGORIES.items():
        if isinstance(exc, exc_type):
            return SideEffectOutcome.AMBIGUOUS, category
    return SideEffectOutcome.FAILED, type(exc).__name__[:100]
