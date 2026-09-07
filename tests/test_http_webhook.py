"""Local-only contract tests for BTN-74 generic HTTP webhook delivery."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time
from typing import Iterator

import pytest
from pydantic import ValidationError

from battalion.integrations.configuration import (
    CapabilitySurface,
    IntegrationConfiguration,
    TransportKind,
)
from battalion.integrations.effects import ReconciliationRequired
from battalion.integrations.events import OutboundEventPublisher, events_for_state
from battalion.integrations.runtime import (
    IntegrationCancelled,
    IntegrationConfigurationError,
    IntegrationRuntime,
    IntegrationTimeout,
)
from battalion.integrations.webhook import (
    WebhookRejected,
    http_webhook_outbound_event_sink_registration,
    http_webhook_transport_factory,
)
from battalion.state.models import InterruptLogEntry, RunState, RunStatus, SideEffectStatus
from support.state import make_run_state


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _state(status: RunStatus = RunStatus.DONE) -> RunState:
    values: dict[str, object] = {
        "run_id": "run-btn-74",
        "run_alias": "BTN-74-slate",
        "project_id": "40000000-0000-4000-8000-000000000074",
        "ticket_id": "BTN-74",
        "spec": "HTTP webhook test.",
        "status": status,
        "phase": "done" if status is RunStatus.DONE else "pause",
        "write_scope": {},
        "budget_limit": 10,
    }
    if status is RunStatus.AWAITING_HUMAN:
        values["interrupt_log"] = [
            InterruptLogEntry(trigger="budget-exceeded", timestamp=NOW, context={})
        ]
    return make_run_state(**values)


@dataclass
class _Endpoint:
    responses: list[tuple[int, float]]
    received: list[dict[str, object]] = field(default_factory=list)


@contextmanager
def _local_endpoint(*responses: tuple[int, float]) -> Iterator[tuple[str, _Endpoint]]:
    endpoint = _Endpoint(list(responses))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP method name
            body = self.rfile.read(int(self.headers["Content-Length"]))
            endpoint.received.append(
                {
                    "body": json.loads(body),
                    "authorization": self.headers.get("Authorization"),
                    "event_id": self.headers.get("Battalion-Event-Id"),
                    "idempotency_key": self.headers.get("Idempotency-Key"),
                    "method": self.command,
                }
            )
            status, delay = endpoint.responses.pop(0)
            if delay:
                time.sleep(delay)
            try:
                self.send_response(status)
                self.end_headers()
            except BrokenPipeError:  # The client timeout is the behavior under test.
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/events", endpoint
    finally:
        server.shutdown()
        thread.join()


def _runtime(
    endpoint: str,
    *,
    event_types: list[str] | None = None,
    timeout_seconds: float = 1,
    cancellation_requested=None,
) -> IntegrationRuntime:
    settings: dict[str, object] = {"endpoint": endpoint, "timeout_seconds": timeout_seconds}
    if event_types is not None:
        settings["event_types"] = event_types
    configuration = IntegrationConfiguration.model_validate(
        {
            "project": {
                "integrations": {
                    "automation": {
                        "integration_id": "automation-webhook",
                        "provider": "http-webhook",
                        "transport": "webhook",
                        "capabilities": ["outbound-event-sink"],
                        "settings": settings,
                        "credential_references": {"authorization": {"reference": "env://TEST_TOKEN"}},
                    }
                }
            }
        }
    )
    return IntegrationRuntime(
        configuration,
        adapters=(http_webhook_outbound_event_sink_registration(),),
        transports={
            TransportKind.WEBHOOK: http_webhook_transport_factory(
                secret_resolver=lambda reference: "Bearer local-test-token",
                cancellation_requested=cancellation_requested,
            )
        },
    )


def test_http_webhook_posts_the_versioned_envelope_with_stable_idempotency():
    with _local_endpoint((204, 0)) as (endpoint, received):
        state = _state()
        event = events_for_state(state, clock=lambda: NOW)[0]
        receipt = OutboundEventPublisher(state, clock=lambda: NOW).publish(
            (event,), sinks=(_runtime(endpoint).outbound_event_sink(),), persist=lambda: None
        )[0]

    assert receipt.status is SideEffectStatus.SUCCEEDED
    assert received.received == [
        {
            "method": "POST",
            "authorization": "Bearer local-test-token",
            "event_id": str(event.event_id),
            "idempotency_key": receipt.operation_id,
            "body": event.model_dump(mode="json"),
        }
    ]
    attempt = state.side_effect_ledger.operations[0].attempts[0]
    assert attempt.provider_idempotency_used is True
    assert attempt.provider_reference == "HTTP 204"


def test_event_type_selection_skips_unsubscribed_events_before_ledger_delivery():
    with _local_endpoint((204, 0)) as (endpoint, received):
        state = _state(RunStatus.AWAITING_HUMAN)
        event = events_for_state(state)[0]
        receipts = OutboundEventPublisher(state).publish(
            (event,),
            sinks=(_runtime(endpoint, event_types=["run_completed"]).outbound_event_sink(),),
            persist=lambda: None,
        )

    assert receipts == ()
    assert received.received == []
    assert state.side_effect_ledger.operations == []


def test_non_success_response_can_retry_with_the_same_event_and_operation_identity():
    with _local_endpoint((503, 0), (204, 0)) as (endpoint, received):
        state = _state()
        event = events_for_state(state, clock=lambda: NOW)[0]
        publisher = OutboundEventPublisher(state, clock=lambda: NOW)
        sink = _runtime(endpoint).outbound_event_sink()

        with pytest.raises(WebhookRejected, match="HTTP 503"):
            publisher.publish((event,), sinks=(sink,), persist=lambda: None)
        receipt = publisher.publish((event,), sinks=(sink,), persist=lambda: None)[0]

    record = state.side_effect_ledger.operations[0]
    assert receipt.operation_id == record.operation_id
    assert [request["event_id"] for request in received.received] == [
        str(event.event_id),
        str(event.event_id),
    ]
    assert [request["idempotency_key"] for request in received.received] == [
        record.operation_id,
        record.operation_id,
    ]
    assert receipt.attempt_number == 2


def test_timeout_is_ambiguous_and_refuses_automatic_redelivery():
    with _local_endpoint((204, 0.15)) as (endpoint, received):
        state = _state()
        event = events_for_state(state, clock=lambda: NOW)[0]
        publisher = OutboundEventPublisher(state, clock=lambda: NOW)
        sink = _runtime(endpoint, timeout_seconds=0.05).outbound_event_sink()

        with pytest.raises(IntegrationTimeout):
            publisher.publish((event,), sinks=(sink,), persist=lambda: None)
        with pytest.raises(ReconciliationRequired):
            publisher.publish((event,), sinks=(sink,), persist=lambda: None)

    assert len(received.received) == 1
    assert state.side_effect_ledger.operations[0].status is SideEffectStatus.AMBIGUOUS


def test_cancelled_delivery_is_ambiguous_without_submitting_a_request():
    with _local_endpoint((204, 0)) as (endpoint, received):
        state = _state()
        event = events_for_state(state, clock=lambda: NOW)[0]
        with pytest.raises(IntegrationCancelled):
            OutboundEventPublisher(state).publish(
                (event,),
                sinks=(
                    _runtime(endpoint, cancellation_requested=lambda: True).outbound_event_sink(),
                ),
                persist=lambda: None,
            )

    assert received.received == []
    assert state.side_effect_ledger.operations[0].status is SideEffectStatus.AMBIGUOUS


def test_webhook_configuration_rejects_secret_settings_and_unsafe_endpoint():
    with pytest.raises(ValidationError, match="credential_references"):
        IntegrationConfiguration.model_validate(
            {
                "project": {
                    "integrations": {
                        "bad-webhook": {
                            "integration_id": "bad-webhook",
                            "provider": "http-webhook",
                            "transport": "webhook",
                            "capabilities": ["outbound-event-sink"],
                            "settings": {"authorization": "literal secret"},
                        }
                    }
                }
            }
        )

    with pytest.raises(IntegrationConfigurationError, match="without credentials"):
        _runtime("https://user:password@example.test/events?token=secret").outbound_event_sink()
