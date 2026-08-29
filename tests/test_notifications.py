"""BTN-75: actor-targeted notification routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from battalion.actors import bootstrap_local_actor, link_external_identity
from battalion.identity import load_project_identity
from battalion.integrations.configuration import CapabilitySurface, IntegrationConfiguration, TransportKind
from battalion.integrations.effects import SideEffectCoordinator
from battalion.integrations.runtime import (
    AdapterBinding,
    AdapterRegistration,
    BoundTransport,
    IntegrationError,
    IntegrationRuntime,
    IntegrationTimeout,
    NotificationDelivery,
    TransportCall,
    TransportOperation,
    TransportResponse,
)
from battalion.notifications import (
    ActorNotificationTarget,
    NotificationClassification,
    NotificationOutcome,
    NotificationRequest,
    NotificationRouter,
)
from conftest import make_run_state


class DeliveryRejected(IntegrationError):
    """A deterministic provider-side rejection."""


@dataclass
class FakeNotificationPort:
    capability: CapabilitySurface = CapabilitySurface.NOTIFICATION
    deliveries: list[NotificationDelivery] = field(default_factory=list)
    failure: Exception | None = None

    def send(self, delivery: NotificationDelivery) -> dict[str, str]:
        self.deliveries.append(delivery)
        if self.failure is not None:
            raise self.failure
        return {"provider_reference": "not-retained-by-fake"}


class FakeNotificationFactory:
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        construction_failure: Exception | None = None,
    ) -> None:
        self.port = FakeNotificationPort(failure=failure)
        self.bindings: list[AdapterBinding] = []
        self.construction_failure = construction_failure

    def __call__(self, binding: AdapterBinding, transport: BoundTransport) -> FakeNotificationPort:
        self.bindings.append(binding)
        if self.construction_failure is not None:
            raise self.construction_failure
        return self.port


class FakeTransport:
    def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
        return TransportResponse(None)


def _configuration(
    actor_id: str,
    *,
    disabled: list[str] | None = None,
    allowed: list[str] | None = None,
) -> IntegrationConfiguration:
    return IntegrationConfiguration.model_validate(
        {
            "organization": {
                "allowed_integrations": allowed or ["discord-primary", "discord-backup"]
            },
            "project": {
                "integrations": {
                    "discord-primary": {
                        "integration_id": "discord-community-one",
                        "provider": "discord",
                        "transport": "native-local",
                        "capabilities": ["notification"],
                    },
                    "discord-backup": {
                        "integration_id": "discord-community-two",
                        "provider": "discord",
                        "transport": "native-local",
                        "capabilities": ["notification"],
                    },
                    "email": {
                        "integration_id": "email-work",
                        "provider": "email",
                        "transport": "native-local",
                        "capabilities": ["notification"],
                    },
                },
                "notification_defaults": ["discord-primary", "discord-backup", "email"],
                "disabled_notification_integrations": disabled or [],
                "notification_actor_groups": {"on-call": [actor_id]},
            },
        }
    )


def _runtime(configuration: IntegrationConfiguration, factories: dict[str, FakeNotificationFactory]) -> IntegrationRuntime:
    registrations = tuple(
        AdapterRegistration(
            provider=provider,
            transport=TransportKind.NATIVE_LOCAL,
            capability=CapabilitySurface.NOTIFICATION,
            required_transport_operations=frozenset(),
            factory=factory,
        )
        for provider, factory in factories.items()
    )
    return IntegrationRuntime(
        configuration,
        adapters=registrations,
        transports={TransportKind.NATIVE_LOCAL: lambda binding: FakeTransport()},
    )


def _request(actor_id) -> NotificationRequest:
    return NotificationRequest(
        logical_notification_id="interrupt-run-42",
        target=ActorNotificationTarget(actor_ids=(actor_id,)),
        classification=NotificationClassification.URGENT,
        template_data={"run": "run-42", "reason": "review-needed"},
        return_route="battalion://runs/run-42/interrupts/current",
    )


def test_notification_routes_an_actor_to_multiple_provider_instances_and_replays_safely(tmp_path):
    load_project_identity(tmp_path, create=True)
    registry = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = registry.local_actor_id
    assert actor_id is not None
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="discord-community-one",
        provider="discord",
        external_subject="member-one",
    )
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="discord-community-two",
        provider="discord",
        external_subject="member-two",
    )
    configuration = _configuration(str(actor_id))
    discord = FakeNotificationFactory()
    email = FakeNotificationFactory()
    runtime = _runtime(configuration, {"discord": discord, "email": email})
    state = make_run_state(ticket_id="BTN-75")
    persists: list[str] = []
    router = NotificationRouter(
        actors=registry,
        runtime=runtime,
        effects=SideEffectCoordinator(state),
        persist=lambda: persists.append("saved"),
    )

    report = router.send(_request(actor_id))
    replay = router.send(_request(actor_id))

    assert [result.outcome for result in report.results] == [
        NotificationOutcome.DELIVERED,
        NotificationOutcome.DELIVERED,
        NotificationOutcome.MISSING_DESTINATION,
    ]
    assert [result.outcome for result in replay.results] == [
        NotificationOutcome.REPLAYED,
        NotificationOutcome.REPLAYED,
        NotificationOutcome.MISSING_DESTINATION,
    ]
    assert [delivery.external_subject for delivery in discord.port.deliveries] == [
        "member-one",
        "member-two",
    ]
    assert all(delivery.logical_notification_id == "interrupt-run-42" for delivery in discord.port.deliveries)
    assert len(state.side_effect_ledger.operations) == 2
    assert persists == ["saved", "saved", "saved", "saved"]


def test_notification_reports_disabled_policy_denied_and_failed_delivery_without_mutating_interrupts(tmp_path):
    load_project_identity(tmp_path, create=True)
    registry = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = registry.local_actor_id
    assert actor_id is not None
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="discord-community-two",
        provider="discord",
        external_subject="member-two",
    )
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="email-work",
        provider="email",
        external_subject="operator@example.test",
    )
    configuration = _configuration(str(actor_id), disabled=["discord-primary"])
    discord = FakeNotificationFactory()
    email = FakeNotificationFactory(failure=DeliveryRejected("recipient rejected"))
    runtime = _runtime(configuration, {"discord": discord, "email": email})
    state = make_run_state(ticket_id="BTN-75")
    interrupt_state = state.model_dump(mode="json")
    router = NotificationRouter(
        actors=registry,
        runtime=runtime,
        effects=SideEffectCoordinator(state),
        persist=lambda: None,
    )

    report = router.send(_request(actor_id))

    assert [result.outcome for result in report.results] == [
        NotificationOutcome.CHANNEL_DISABLED,
        NotificationOutcome.DELIVERED,
        NotificationOutcome.POLICY_DENIED,
    ]
    assert report.results[-1].failure_kind == "IntegrationPolicyDenied"
    assert state.model_dump(mode="json") | {"side_effect_ledger": interrupt_state["side_effect_ledger"]} == interrupt_state
    assert len(state.side_effect_ledger.operations) == 1
    assert state.side_effect_ledger.operations[0].status.value == "succeeded"


def test_notification_reports_confirmed_delivery_failure_and_resolves_named_actor_group(tmp_path):
    load_project_identity(tmp_path, create=True)
    registry = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = registry.local_actor_id
    assert actor_id is not None
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="email-work",
        provider="email",
        external_subject="operator@example.test",
    )
    configuration = _configuration(
        str(actor_id),
        disabled=["discord-primary", "discord-backup"],
        allowed=["discord-primary", "discord-backup", "email"],
    )
    discord = FakeNotificationFactory()
    email = FakeNotificationFactory(failure=DeliveryRejected("recipient rejected"))
    runtime = _runtime(configuration, {"discord": discord, "email": email})
    state = make_run_state(ticket_id="BTN-75")
    router = NotificationRouter(
        actors=registry,
        runtime=runtime,
        effects=SideEffectCoordinator(state),
        persist=lambda: None,
    )
    request = NotificationRequest(
        logical_notification_id="interrupt-run-43",
        target=ActorNotificationTarget(actor_group="on-call"),
        classification=NotificationClassification.NORMAL,
        template_data={"run": "run-43"},
        return_route="battalion://runs/run-43",
    )

    report = router.send(request)

    assert [result.outcome for result in report.results] == [
        NotificationOutcome.CHANNEL_DISABLED,
        NotificationOutcome.CHANNEL_DISABLED,
        NotificationOutcome.DELIVERY_FAILED,
    ]
    assert report.results[-1].failure_kind == "DeliveryRejected"
    assert state.side_effect_ledger.operations[0].status.value == "failed"


def test_actor_preference_selects_one_permitted_notification_channel(tmp_path):
    load_project_identity(tmp_path, create=True)
    registry = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = registry.local_actor_id
    assert actor_id is not None
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="discord-community-one",
        provider="discord",
        external_subject="member-one",
    )
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="discord-community-two",
        provider="discord",
        external_subject="member-two",
    )
    data = _configuration(str(actor_id)).model_dump(mode="json")
    data["actor_preferences"] = {
        str(actor_id): {"preferred_integrations": {"notification": "discord-backup"}}
    }
    configuration = IntegrationConfiguration.model_validate(data)
    discord = FakeNotificationFactory()
    runtime = _runtime(configuration, {"discord": discord, "email": FakeNotificationFactory()})
    state = make_run_state(ticket_id="BTN-75")
    router = NotificationRouter(
        actors=registry,
        runtime=runtime,
        effects=SideEffectCoordinator(state),
        persist=lambda: None,
    )

    report = router.send(_request(actor_id))

    assert [result.integration_name for result in report.results] == ["discord-backup"]
    assert [delivery.external_subject for delivery in discord.port.deliveries] == ["member-two"]


def test_notification_reports_unavailable_and_ambiguous_delivery_separately(tmp_path):
    load_project_identity(tmp_path, create=True)
    registry = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = registry.local_actor_id
    assert actor_id is not None
    registry = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="email-work",
        provider="email",
        external_subject="operator@example.test",
    )
    configuration = _configuration(
        str(actor_id),
        disabled=["discord-primary", "discord-backup"],
        allowed=["discord-primary", "discord-backup", "email"],
    )
    unavailable_runtime = _runtime(
        configuration,
        {
            "discord": FakeNotificationFactory(),
            "email": FakeNotificationFactory(construction_failure=ValueError("offline")),
        },
    )
    state = make_run_state(ticket_id="BTN-75")
    unavailable = NotificationRouter(
        actors=registry,
        runtime=unavailable_runtime,
        effects=SideEffectCoordinator(state),
        persist=lambda: None,
    ).send(_request(actor_id))
    timeout_runtime = _runtime(
        configuration,
        {
            "discord": FakeNotificationFactory(),
            "email": FakeNotificationFactory(failure=IntegrationTimeout("timed out")),
        },
    )
    ambiguous = NotificationRouter(
        actors=registry,
        runtime=timeout_runtime,
        effects=SideEffectCoordinator(make_run_state(ticket_id="BTN-75-timeout")),
        persist=lambda: None,
    ).send(_request(actor_id))

    assert unavailable.results[-1].outcome is NotificationOutcome.INTEGRATION_UNAVAILABLE
    assert ambiguous.results[-1].outcome is NotificationOutcome.DELIVERY_AMBIGUOUS
    assert ambiguous.results[-1].operation_id is not None
