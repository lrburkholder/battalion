"""Actor-targeted, provider-neutral human notification routing (BTN-75).

This module is the only notification-layer code that sees both Battalion Actor
identity and configured provider bindings.  Callers provide an Actor target or
named project group; adapters receive a resolved provider subject only for the
single delivery they perform.  The router never receives a HumanInterrupt and
therefore cannot resolve, dismiss, or otherwise mutate one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from battalion.actors import ActorRegistry, ActorStatus
from battalion.integrations.configuration import CapabilitySurface, IntegrationDefinition
from battalion.integrations.effects import (
    DeliveryReceipt,
    ReconciliationRequired,
    SideEffectCoordinator,
    request_digest,
)
from battalion.integrations.runtime import (
    IntegrationError,
    IntegrationCancelled,
    IntegrationMalformedResponse,
    IntegrationPolicyDenied,
    IntegrationRuntime,
    IntegrationTimeout,
    IntegrationTransportFailure,
    IntegrationUnavailable,
    NotificationDelivery,
)


class NotificationRoutingError(ValueError):
    """A caller supplied a notification outside Battalion's bounded contract."""


class NotificationClassification(str, Enum):
    """The initial bounded classes used by notification policy and templates."""

    INFORMATIONAL = "informational"
    NORMAL = "normal"
    URGENT = "urgent"


class NotificationOutcome(str, Enum):
    """Inspectable result for one Actor/channel delivery path."""

    DELIVERED = "delivered"
    REPLAYED = "replayed"
    MISSING_DESTINATION = "missing-destination"
    CHANNEL_DISABLED = "channel-disabled"
    INTEGRATION_UNAVAILABLE = "integration-unavailable"
    POLICY_DENIED = "policy-denied"
    DELIVERY_FAILED = "delivery-failed"
    DELIVERY_AMBIGUOUS = "delivery-ambiguous"
    ACTOR_UNAVAILABLE = "actor-unavailable"


@dataclass(frozen=True)
class ActorNotificationTarget:
    """One or more explicit Actors, or one named project-defined Actor group."""

    actor_ids: tuple[UUID, ...] = ()
    actor_group: str | None = None

    def __post_init__(self) -> None:
        if bool(self.actor_ids) == (self.actor_group is not None):
            raise NotificationRoutingError(
                "notification target must contain Actor IDs or exactly one Actor group"
            )
        if len(self.actor_ids) != len(set(self.actor_ids)):
            raise NotificationRoutingError("notification target must not repeat an Actor")


@dataclass(frozen=True)
class NotificationRequest:
    """A bounded logical human notification without provider destination data."""

    logical_notification_id: str
    target: ActorNotificationTarget
    classification: NotificationClassification
    template_data: Mapping[str, Any]
    return_route: str

    def __post_init__(self) -> None:
        if not self.logical_notification_id or len(self.logical_notification_id) > 48:
            raise NotificationRoutingError(
                "logical notification ID must contain between 1 and 48 characters"
            )
        if not self.return_route or len(self.return_route) > 500:
            raise NotificationRoutingError("notification return route must contain 1 to 500 characters")
        try:
            encoded = json.dumps(self.template_data, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise NotificationRoutingError("notification template data must be JSON data") from exc
        if len(encoded) > 4000:
            raise NotificationRoutingError("notification template data exceeds 4000 characters")


@dataclass(frozen=True)
class NotificationDeliveryResult:
    """One reportable route outcome, deliberately excluding provider destinations."""

    actor_id: UUID
    integration_name: str
    integration_id: str
    outcome: NotificationOutcome
    operation_id: str | None = None
    failure_kind: str | None = None


@dataclass(frozen=True)
class NotificationReport:
    """All channel-level results for one logical notification."""

    logical_notification_id: str
    results: tuple[NotificationDeliveryResult, ...]


class NotificationRouter:
    """Resolve Actor targets, apply permitted channel selection, and deliver.

    A ``SideEffectCoordinator`` plus persistence callback are supplied by the
    application boundary.  This makes durable intent and attempt recording an
    enforced part of every provider call, while keeping the router free of
    direct filesystem and Run-mutation authority.
    """

    def __init__(
        self,
        *,
        actors: ActorRegistry,
        runtime: IntegrationRuntime,
        effects: SideEffectCoordinator,
        persist: Callable[[], None],
    ) -> None:
        self._actors = actors
        self._runtime = runtime
        self._effects = effects
        self._persist = persist

    def send(self, request: NotificationRequest) -> NotificationReport:
        """Route a logical notification without changing the underlying Run state."""

        actor_ids = self._resolve_target(request.target)
        actors_by_id = {actor.actor_id: actor for actor in self._actors.actors}
        results: list[NotificationDeliveryResult] = []
        for actor_id in actor_ids:
            actor = actors_by_id.get(actor_id)
            if actor is None or actor.status is not ActorStatus.ACTIVE:
                results.append(
                    NotificationDeliveryResult(
                        actor_id=actor_id,
                        integration_name="unresolved",
                        integration_id="unresolved",
                        outcome=NotificationOutcome.ACTOR_UNAVAILABLE,
                    )
                )
                continue
            for name in self._select_channels(actor_id):
                definition = self._runtime.configuration.project.integrations[name]
                results.extend(self._send_on_channel(request, actor_id, name, definition))
        return NotificationReport(request.logical_notification_id, tuple(results))

    def _resolve_target(self, target: ActorNotificationTarget) -> tuple[UUID, ...]:
        if target.actor_group is None:
            return target.actor_ids
        group = self._runtime.configuration.project.notification_actor_groups.get(
            target.actor_group
        )
        if group is None:
            raise NotificationRoutingError(
                f"notification Actor group {target.actor_group!r} is not configured"
            )
        return group

    def _select_channels(self, actor_id: UUID) -> tuple[str, ...]:
        configuration = self._runtime.configuration
        preference = configuration.actor_preferences.get(str(actor_id))
        if preference is not None:
            selected = preference.preferred_integrations.get(CapabilitySurface.NOTIFICATION)
            if selected is not None:
                return (selected,)
        defaults = configuration.project.notification_defaults
        if defaults:
            return defaults
        return tuple(
            name
            for name, definition in configuration.project.integrations.items()
            if CapabilitySurface.NOTIFICATION in definition.capabilities
        )

    def _send_on_channel(
        self,
        request: NotificationRequest,
        actor_id: UUID,
        name: str,
        definition: IntegrationDefinition,
    ) -> list[NotificationDeliveryResult]:
        if name in self._runtime.configuration.project.disabled_notification_integrations:
            return [
                self._result(actor_id, name, definition, NotificationOutcome.CHANNEL_DISABLED)
            ]

        identities = tuple(
            identity
            for identity in self._actors.external_identities
            if (
                identity.actor_id == actor_id
                and identity.integration_id == definition.integration_id
                and identity.provider == definition.provider
            )
        )
        if not identities:
            return [
                self._result(actor_id, name, definition, NotificationOutcome.MISSING_DESTINATION)
            ]

        try:
            port = self._runtime.notification(name)
        except IntegrationPolicyDenied as exc:
            return [
                self._result(
                    actor_id,
                    name,
                    definition,
                    NotificationOutcome.POLICY_DENIED,
                    failure_kind=type(exc).__name__,
                )
            ]
        except (IntegrationUnavailable, IntegrationError) as exc:
            return [
                self._result(
                    actor_id,
                    name,
                    definition,
                    NotificationOutcome.INTEGRATION_UNAVAILABLE,
                    failure_kind=type(exc).__name__,
                )
            ]

        return [
            self._deliver(request, actor_id, name, definition, port, identity.external_subject)
            for identity in identities
        ]

    def _deliver(
        self,
        request: NotificationRequest,
        actor_id: UUID,
        name: str,
        definition: IntegrationDefinition,
        port: Any,
        external_subject: str,
    ) -> NotificationDeliveryResult:
        subject_digest = hashlib.sha256(external_subject.encode("utf-8")).hexdigest()[:32]
        dedupe_key = (
            f"notification:{request.logical_notification_id}:{actor_id}:"
            f"{definition.integration_id}:{subject_digest}"
        )
        immutable_template_data = MappingProxyType(
            json.loads(json.dumps(request.template_data, sort_keys=True))
        )
        digest = request_digest(
            {
                "logical_notification_id": request.logical_notification_id,
                "actor_id": str(actor_id),
                "integration_id": definition.integration_id,
                "classification": request.classification.value,
                "template_data": dict(immutable_template_data),
                "return_route": request.return_route,
            }
        )
        try:
            receipt = self._effects.execute(
                capability=CapabilitySurface.NOTIFICATION,
                integration_id=definition.integration_id,
                integration_name=name,
                provider=definition.provider,
                transport=definition.transport,
                operation="notification.send",
                actor_id=actor_id,
                dedupe_key=dedupe_key,
                request_digest_value=digest,
                persist=self._persist,
                deliver=lambda operation_id: port.send(
                    NotificationDelivery(
                        logical_notification_id=request.logical_notification_id,
                        classification=request.classification.value,
                        template_data=immutable_template_data,
                        return_route=request.return_route,
                        external_subject=external_subject,
                        idempotency_key=operation_id,
                    )
                ),
            )
        except ReconciliationRequired as exc:
            return self._result(
                actor_id,
                name,
                definition,
                NotificationOutcome.DELIVERY_AMBIGUOUS,
                operation_id=exc.operation.operation_id,
                failure_kind=type(exc).__name__,
            )
        except (
            IntegrationTimeout,
            IntegrationCancelled,
            IntegrationTransportFailure,
            IntegrationMalformedResponse,
        ) as exc:
            record = self._effects.find_by_key(dedupe_key)
            return self._result(
                actor_id,
                name,
                definition,
                NotificationOutcome.DELIVERY_AMBIGUOUS,
                operation_id=record.operation_id if record is not None else None,
                failure_kind=type(exc).__name__,
            )
        except IntegrationError as exc:
            return self._result(
                actor_id,
                name,
                definition,
                NotificationOutcome.DELIVERY_FAILED,
                failure_kind=type(exc).__name__,
            )
        return self._receipt_result(actor_id, name, definition, receipt)

    @staticmethod
    def _receipt_result(
        actor_id: UUID,
        name: str,
        definition: IntegrationDefinition,
        receipt: DeliveryReceipt,
    ) -> NotificationDeliveryResult:
        return NotificationDeliveryResult(
            actor_id=actor_id,
            integration_name=name,
            integration_id=definition.integration_id,
            outcome=(NotificationOutcome.REPLAYED if receipt.replayed else NotificationOutcome.DELIVERED),
            operation_id=receipt.operation_id,
        )

    @staticmethod
    def _result(
        actor_id: UUID,
        name: str,
        definition: IntegrationDefinition,
        outcome: NotificationOutcome,
        *,
        operation_id: str | None = None,
        failure_kind: str | None = None,
    ) -> NotificationDeliveryResult:
        return NotificationDeliveryResult(
            actor_id=actor_id,
            integration_name=name,
            integration_id=definition.integration_id,
            outcome=outcome,
            operation_id=operation_id,
            failure_kind=failure_kind,
        )
