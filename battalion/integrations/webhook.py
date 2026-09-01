"""Generic HTTP webhook delivery for outbound Battalion events (BTN-74).

This module intentionally knows no automation vendor.  The adapter accepts only
the versioned :class:`OutboundEvent` envelope and a Battalion-minted operation
ID; the transport owns one configured HTTP endpoint and resolves optional
authorization material through an injected secret resolver.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
import socket
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from battalion.integrations.configuration import CapabilitySurface, SecretReference, TransportKind
from battalion.integrations.effects import ProviderEvidence
from battalion.integrations.events import OutboundEvent, OutboundEventType
from battalion.integrations.runtime import (
    AdapterBinding,
    AdapterRegistration,
    BoundTransport,
    IntegrationCancelled,
    IntegrationConfigurationError,
    IntegrationError,
    IntegrationMalformedResponse,
    IntegrationTimeout,
    IntegrationTransportFailure,
    TransportCall,
    TransportOperation,
    TransportResponse,
)


class WebhookRejected(IntegrationError):
    """A webhook endpoint confirmed that it did not accept an event."""


class SecretResolver(Protocol):
    """Resolve one approved symbolic secret reference below application policy."""

    def __call__(self, reference: SecretReference) -> str:
        """Return the credential value without persisting or logging it."""


def environment_secret_resolver(reference: SecretReference) -> str:
    """Resolve an ``env://`` reference for the built-in HTTP transport.

    Keyring references remain valid portable configuration, but require an
    environment-specific resolver to be injected when constructing the
    transport.  They must never be treated as literal credentials.
    """

    if not reference.reference.startswith("env://"):
        raise IntegrationConfigurationError(
            "the built-in HTTP webhook transport requires an injected resolver "
            "for non-environment credential references"
        )
    value = os.environ.get(reference.reference.removeprefix("env://"))
    if value is None:
        raise IntegrationConfigurationError("configured webhook credential is unavailable")
    return value


@dataclass(frozen=True)
class HttpWebhookTransport:
    """One configured, redirect-free HTTP POST endpoint.

    The transport deliberately accepts no arbitrary URL, headers, or HTTP
    method from an adapter.  It only performs the ``webhook.deliver`` mechanic
    configured for this binding.
    """

    endpoint: str
    timeout_seconds: float
    authorization: str | None
    cancellation_requested: Callable[[], bool]

    @classmethod
    def from_binding(
        cls,
        binding: AdapterBinding,
        *,
        secret_resolver: SecretResolver = environment_secret_resolver,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> "HttpWebhookTransport":
        endpoint, timeout_seconds = _webhook_settings(binding.settings)
        authorization_reference = binding.credential_references.get("authorization")
        unexpected_references = set(binding.credential_references) - {"authorization"}
        if unexpected_references:
            raise IntegrationConfigurationError(
                "generic HTTP webhook credentials may only use the "
                "'authorization' symbolic reference"
            )
        authorization = (
            secret_resolver(authorization_reference)
            if authorization_reference is not None
            else None
        )
        if not isinstance(authorization, str) and authorization is not None:
            raise IntegrationConfigurationError("webhook credential resolver returned non-text data")
        return cls(
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
            authorization=authorization,
            cancellation_requested=cancellation_requested or (lambda: False),
        )

    def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
        if operation is not TransportOperation.WEBHOOK_DELIVER:
            raise IntegrationMalformedResponse("HTTP webhook transport only permits webhook delivery")
        body, idempotency_key, event_id = _delivery_call(call.payload)
        if self.cancellation_requested():
            raise IntegrationCancelled("webhook delivery was cancelled before submission")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "Battalion-Event-Id": event_id,
            "User-Agent": "battalion-webhook/1.0",
        }
        if self.authorization is not None:
            headers["Authorization"] = self.authorization
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                status = response.status
        except HTTPError as exc:
            # HTTP status is a confirmed remote response, never a transport
            # ambiguity. The adapter turns it into a retryable known rejection.
            status = exc.code
        except (TimeoutError, socket.timeout) as exc:
            raise IntegrationTimeout("webhook request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise IntegrationTimeout("webhook request timed out") from exc
            raise IntegrationTransportFailure("webhook endpoint is unavailable") from exc
        except OSError as exc:
            raise IntegrationTransportFailure("webhook endpoint is unavailable") from exc

        if self.cancellation_requested():
            raise IntegrationCancelled("webhook delivery was cancelled during submission")
        return TransportResponse({"status": status})


class _NoRedirect(HTTPRedirectHandler):
    """Keep a configured destination from silently redirecting to another host."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True)
class HttpWebhookOutboundEventSink:
    """Provider-neutral adapter that selects and serializes registered events."""

    integration_id: str
    selected_event_types: frozenset[OutboundEventType]
    transport: BoundTransport
    capability: CapabilitySurface = CapabilitySurface.OUTBOUND_EVENT_SINK

    def accepts(self, event: OutboundEvent) -> bool:
        """Return whether this configured sink is subscribed to the event type."""

        return event.event_type in self.selected_event_types

    def publish(self, event: OutboundEvent, *, idempotency_key: str) -> ProviderEvidence:
        if not self.accepts(event):
            raise IntegrationMalformedResponse("attempted to publish an unselected webhook event")
        response = self.transport.invoke(
            TransportOperation.WEBHOOK_DELIVER,
            TransportCall(
                {
                    "body": event.model_dump(mode="json"),
                    "event_id": str(event.event_id),
                    "idempotency_key": idempotency_key,
                }
            ),
        ).payload
        if not isinstance(response, Mapping) or isinstance(response.get("status"), bool):
            raise IntegrationMalformedResponse("webhook transport returned no integer HTTP status")
        status = response["status"]
        if not isinstance(status, int):
            raise IntegrationMalformedResponse("webhook transport returned no integer HTTP status")
        if not 200 <= status < 300:
            raise WebhookRejected(f"webhook endpoint returned HTTP {status}")
        return ProviderEvidence(
            provider_idempotency_used=True,
            provider_reference=f"HTTP {status}",
        )


def http_webhook_transport_factory(
    *,
    secret_resolver: SecretResolver = environment_secret_resolver,
    cancellation_requested: Callable[[], bool] | None = None,
) -> Callable[[AdapterBinding], HttpWebhookTransport]:
    """Build the bounded transport factory for generic HTTP webhook bindings."""

    return lambda binding: HttpWebhookTransport.from_binding(
        binding,
        secret_resolver=secret_resolver,
        cancellation_requested=cancellation_requested,
    )


def http_webhook_outbound_event_sink_factory(
    binding: AdapterBinding, transport: BoundTransport
) -> HttpWebhookOutboundEventSink:
    """Construct a generic outbound sink without leaking endpoint or secrets."""

    if binding.provider != "http-webhook" or binding.transport is not TransportKind.WEBHOOK:
        raise IntegrationMalformedResponse(
            "generic outbound webhook requires the http-webhook/webhook binding"
        )
    _webhook_settings(binding.settings)
    return HttpWebhookOutboundEventSink(
        integration_id=binding.integration_id,
        selected_event_types=_selected_event_types(binding.settings),
        transport=transport,
    )


def http_webhook_outbound_event_sink_registration() -> AdapterRegistration:
    """Return the explicit generic HTTP webhook adapter registration."""

    return AdapterRegistration(
        provider="http-webhook",
        transport=TransportKind.WEBHOOK,
        capability=CapabilitySurface.OUTBOUND_EVENT_SINK,
        required_transport_operations=frozenset({TransportOperation.WEBHOOK_DELIVER}),
        factory=http_webhook_outbound_event_sink_factory,
    )


def _webhook_settings(settings: Mapping[str, Any]) -> tuple[str, float]:
    allowed = {"endpoint", "event_types", "timeout_seconds"}
    unknown = set(settings) - allowed
    if unknown:
        raise IntegrationConfigurationError(
            "generic HTTP webhook settings contain unsupported keys: "
            + ", ".join(sorted(unknown))
        )
    endpoint = settings.get("endpoint")
    if not isinstance(endpoint, str):
        raise IntegrationConfigurationError("generic HTTP webhook settings require an endpoint")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise IntegrationConfigurationError(
            "webhook endpoint must be an HTTP(S) URL without credentials, query, or fragment"
        )
    timeout = settings.get("timeout_seconds", 10.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise IntegrationConfigurationError("webhook timeout_seconds must be a number")
    timeout_seconds = float(timeout)
    if not 0 < timeout_seconds <= 60:
        raise IntegrationConfigurationError("webhook timeout_seconds must be greater than 0 and at most 60")
    _selected_event_types(settings)
    return endpoint, timeout_seconds


def _selected_event_types(settings: Mapping[str, Any]) -> frozenset[OutboundEventType]:
    configured = settings.get("event_types")
    if configured is None:
        return frozenset(OutboundEventType)
    if not isinstance(configured, (list, tuple, frozenset)) or not configured:
        raise IntegrationConfigurationError("webhook event_types must be a non-empty list")
    values: list[OutboundEventType] = []
    for value in configured:
        try:
            values.append(OutboundEventType(value))
        except (TypeError, ValueError) as exc:
            raise IntegrationConfigurationError(
                "webhook event_types must name registered outbound event types"
            ) from exc
    if len(set(values)) != len(values):
        raise IntegrationConfigurationError("webhook event_types must not contain duplicates")
    return frozenset(values)


def _delivery_call(payload: Any) -> tuple[bytes, str, str]:
    if not isinstance(payload, Mapping):
        raise IntegrationMalformedResponse("webhook delivery call must be a mapping")
    body = payload.get("body")
    idempotency_key = payload.get("idempotency_key")
    event_id = payload.get("event_id")
    if not isinstance(body, Mapping):
        raise IntegrationMalformedResponse("webhook delivery call must contain a JSON object body")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise IntegrationMalformedResponse("webhook delivery call must contain an idempotency key")
    if not isinstance(event_id, str) or not event_id:
        raise IntegrationMalformedResponse("webhook delivery call must contain an event ID")
    return (
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        idempotency_key,
        event_id,
    )
