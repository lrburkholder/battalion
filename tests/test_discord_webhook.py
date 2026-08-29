"""BTN-79: deterministic contract tests for Discord human-interrupt delivery."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from battalion.integrations.configuration import (
    CapabilitySurface,
    IntegrationConfiguration,
    SecretReference,
    TransportKind,
)
from battalion.integrations.discord import (
    DiscordWebhookTransport,
    discord_human_interrupt_sink_registration,
)
from battalion.integrations.effects import ReconciliationRequired
from battalion.integrations.events import OutboundEventPublisher, events_for_state
from battalion.integrations.runtime import (
    AdapterBinding,
    IntegrationConfigurationError,
    IntegrationRuntime,
    IntegrationTimeout,
    TransportCall,
    TransportOperation,
    TransportResponse,
)
from battalion.integrations.webhook import WebhookRejected
from battalion.state.models import Budget, InterruptLogEntry, RunState, RunStatus, SideEffectStatus


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class _FakeDiscordHttp:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[TransportOperation, TransportCall]] = []

    def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
        self.calls.append((operation, call))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return TransportResponse(response)


def _state(status: RunStatus = RunStatus.AWAITING_HUMAN) -> RunState:
    state = RunState(
        schema_version="1.0",
        run_id="run-btn-79",
        run_alias="BTN-79-discord",
        project_id="50000000-0000-4000-8000-000000000079",
        ticket_id="BTN-79",
        spec="Discord notification test.",
        status=status,
        phase="review",
        write_scope={},
        retry_bound=2,
        budget=Budget(limit=10),
    )
    if status is RunStatus.AWAITING_HUMAN:
        return state.model_copy(
            update={
                "interrupt_log": [
                    InterruptLogEntry(trigger="same-root-cause", timestamp=NOW, context={})
                ]
            }
        )
    return state


def _runtime(http: _FakeDiscordHttp) -> IntegrationRuntime:
    configuration = IntegrationConfiguration.model_validate(
        {
            "project": {
                "integrations": {
                    "discord-operations": {
                        "integration_id": "discord-operations-primary",
                        "provider": "discord",
                        "transport": "webhook",
                        "capabilities": ["outbound-event-sink"],
                        "settings": {"webhook_id": "123456789012345678"},
                        "credential_references": {
                            "webhook_token": {"reference": "env://DISCORD_WEBHOOK_TOKEN"}
                        },
                    }
                }
            }
        }
    )
    return IntegrationRuntime(
        configuration,
        adapters=(discord_human_interrupt_sink_registration(),),
        transports={TransportKind.WEBHOOK: lambda binding: http},
    )


def test_discord_sink_delivers_a_bounded_human_interrupt_without_run_authority():
    state = _state()
    event = events_for_state(state)[0]
    http = _FakeDiscordHttp({"status": 204})

    receipt = OutboundEventPublisher(state).publish(
        (event,), sinks=(_runtime(http).outbound_event_sink(),), persist=lambda: None
    )[0]

    assert receipt.status is SideEffectStatus.SUCCEEDED
    assert len(http.calls) == 1
    operation, call = http.calls[0]
    assert operation is TransportOperation.WEBHOOK_DELIVER
    assert call.payload == {
        "body": {
            "content": (
                "Battalion requires human review.\n"
                'Run: "run-btn-79"\n'
                'Work item: "BTN-79"\n'
                'Phase: "review"\n'
                'Reason: "same-root-cause"\n'
                'Return to Battalion: battalion status "run-btn-79" --human'
            ),
            "allowed_mentions": {"parse": []},
        },
        "event_id": str(event.event_id),
        "idempotency_key": receipt.operation_id,
    }
    sink = _runtime(_FakeDiscordHttp({"status": 204})).outbound_event_sink()
    assert not hasattr(sink, "state")
    assert not hasattr(sink, "resume")


def test_discord_sink_ignores_non_interrupt_events_before_opening_a_delivery():
    state = _state(RunStatus.DONE)
    http = _FakeDiscordHttp({"status": 204})

    receipts = OutboundEventPublisher(state).publish(
        events_for_state(state, clock=lambda: NOW),
        sinks=(_runtime(http).outbound_event_sink(),),
        persist=lambda: None,
    )

    assert receipts == ()
    assert http.calls == []
    assert state.side_effect_ledger.operations == []


def test_discord_rejection_retries_and_timeout_requires_common_reconciliation():
    state = _state()
    event = events_for_state(state)[0]
    http = _FakeDiscordHttp({"status": 503}, {"status": 204})
    publisher = OutboundEventPublisher(state)
    sink = _runtime(http).outbound_event_sink()

    with pytest.raises(WebhookRejected, match="HTTP 503"):
        publisher.publish((event,), sinks=(sink,), persist=lambda: None)
    receipt = publisher.publish((event,), sinks=(sink,), persist=lambda: None)[0]

    record = state.side_effect_ledger.operations[0]
    assert receipt.operation_id == record.operation_id
    assert [call.payload["idempotency_key"] for _, call in http.calls] == [
        record.operation_id,
        record.operation_id,
    ]

    timed_out = _state()
    timeout_event = events_for_state(timed_out)[0]
    timeout_publisher = OutboundEventPublisher(timed_out)
    timeout_sink = _runtime(_FakeDiscordHttp(IntegrationTimeout("response lost"))).outbound_event_sink()
    with pytest.raises(IntegrationTimeout):
        timeout_publisher.publish((timeout_event,), sinks=(timeout_sink,), persist=lambda: None)
    with pytest.raises(ReconciliationRequired):
        timeout_publisher.publish((timeout_event,), sinks=(timeout_sink,), persist=lambda: None)


def test_discord_webhook_url_is_resolved_only_from_the_secret_boundary():
    secret = "super-secret-discord-webhook-token"
    binding = AdapterBinding(
        integration_id="discord-operations-primary",
        provider="discord",
        transport=TransportKind.WEBHOOK,
        capability=CapabilitySurface.OUTBOUND_EVENT_SINK,
        settings={"webhook_id": "123456789012345678"},
        credential_references={
            "webhook_token": SecretReference(reference="env://DISCORD_WEBHOOK_TOKEN")
        },
    )

    transport = DiscordWebhookTransport.from_binding(
        binding, secret_resolver=lambda reference: secret
    )

    assert secret not in repr(transport)
    with pytest.raises(IntegrationConfigurationError, match="exactly one"):
        DiscordWebhookTransport.from_binding(
            binding.__class__(
                integration_id=binding.integration_id,
                provider=binding.provider,
                transport=binding.transport,
                capability=binding.capability,
                settings=binding.settings,
                credential_references={},
            )
        )

    with pytest.raises(ValidationError, match="credential_references"):
        IntegrationConfiguration.model_validate(
            {
                "project": {
                    "integrations": {
                        "discord-operations": {
                            "integration_id": "discord-operations-primary",
                            "provider": "discord",
                            "transport": "webhook",
                            "capabilities": ["outbound-event-sink"],
                            "settings": {
                                "webhook_id": "123456789012345678",
                                "webhook_token": secret,
                            },
                        }
                    }
                }
            }
        )
