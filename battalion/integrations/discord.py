"""Outbound-only Discord webhook delivery for HumanInterrupt events (BTN-79).

Discord webhook identity is deliberately resolved here, below the
``OutboundEventSink`` boundary.  The adapter receives only a minimized event
envelope and has no inbound, Actor, graph, or Run-mutation capability.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import re
from typing import Any
from urllib.parse import quote

from battalion.integrations.configuration import CapabilitySurface, TransportKind
from battalion.integrations.effects import ProviderEvidence
from battalion.integrations.events import HumanInterruptData, OutboundEvent, OutboundEventType
from battalion.integrations.runtime import (
    AdapterBinding,
    AdapterRegistration,
    BoundTransport,
    IntegrationConfigurationError,
    IntegrationMalformedResponse,
    TransportCall,
    TransportOperation,
)
from battalion.integrations.webhook import (
    HttpWebhookTransport,
    SecretResolver,
    WebhookRejected,
    environment_secret_resolver,
)


_DISCORD_WEBHOOK_ID = re.compile(r"^[0-9]{1,32}$")


@dataclass(frozen=True)
class DiscordWebhookTransport:
    """One Discord webhook endpoint, with its token kept out of configuration.

    The wrapped generic transport provides the shared timeout, redirect, and
    typed-failure semantics.  Its endpoint contains the resolved webhook token,
    so it is intentionally not exposed or rendered in this adapter's repr.
    """

    _transport: HttpWebhookTransport = field(repr=False)

    @classmethod
    def from_binding(
        cls,
        binding: AdapterBinding,
        *,
        secret_resolver: SecretResolver = environment_secret_resolver,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> "DiscordWebhookTransport":
        webhook_id, timeout_seconds = _discord_settings(binding.settings)
        references = binding.credential_references
        if set(references) != {"webhook_token"}:
            raise IntegrationConfigurationError(
                "Discord webhook requires exactly one 'webhook_token' symbolic reference"
            )
        token = secret_resolver(references["webhook_token"])
        if not isinstance(token, str) or not token:
            raise IntegrationConfigurationError("configured Discord webhook credential is unavailable")
        endpoint = "https://discord.com/api/webhooks/" + webhook_id + "/" + quote(token, safe="")
        return cls(
            HttpWebhookTransport(
                endpoint=endpoint,
                timeout_seconds=timeout_seconds,
                authorization=None,
                cancellation_requested=cancellation_requested or (lambda: False),
            )
        )

    def invoke(self, operation: TransportOperation, call: TransportCall):
        """Delegate the one granted webhook mechanic without revealing its URL."""

        return self._transport.invoke(operation, call)


@dataclass(frozen=True)
class DiscordHumanInterruptSink:
    """Render the bounded HumanInterrupt event as a Discord webhook message."""

    integration_id: str
    transport: BoundTransport
    capability: CapabilitySurface = CapabilitySurface.OUTBOUND_EVENT_SINK

    def accepts(self, event: OutboundEvent) -> bool:
        return event.event_type is OutboundEventType.HUMAN_INTERRUPT

    def publish(self, event: OutboundEvent, *, idempotency_key: str) -> ProviderEvidence:
        if not self.accepts(event):
            raise IntegrationMalformedResponse("Discord sink accepts only human_interrupt events")
        response = self.transport.invoke(
            TransportOperation.WEBHOOK_DELIVER,
            TransportCall(
                {
                    "body": _discord_payload(event),
                    "event_id": str(event.event_id),
                    "idempotency_key": idempotency_key,
                }
            ),
        ).payload
        if not isinstance(response, Mapping) or isinstance(response.get("status"), bool):
            raise IntegrationMalformedResponse("Discord webhook returned no integer HTTP status")
        status = response["status"]
        if not isinstance(status, int):
            raise IntegrationMalformedResponse("Discord webhook returned no integer HTTP status")
        if not 200 <= status < 300:
            raise WebhookRejected(f"Discord webhook returned HTTP {status}")
        # Discord's incoming-webhook API does not expose idempotency support.
        # BTN-70 nevertheless prevents replay after a confirmed success and
        # requires reconciliation after an ambiguous network outcome.
        return ProviderEvidence(provider_idempotency_used=False, provider_reference=f"HTTP {status}")


def discord_webhook_transport_factory(
    *,
    secret_resolver: SecretResolver = environment_secret_resolver,
    cancellation_requested: Callable[[], bool] | None = None,
) -> Callable[[AdapterBinding], DiscordWebhookTransport]:
    """Build the Discord-specific webhook transport factory."""

    return lambda binding: DiscordWebhookTransport.from_binding(
        binding,
        secret_resolver=secret_resolver,
        cancellation_requested=cancellation_requested,
    )


def discord_human_interrupt_sink_factory(
    binding: AdapterBinding, transport: BoundTransport
) -> DiscordHumanInterruptSink:
    """Construct the only approved Discord capability in this ticket."""

    if (
        binding.provider != "discord"
        or binding.transport is not TransportKind.WEBHOOK
        or binding.capability is not CapabilitySurface.OUTBOUND_EVENT_SINK
    ):
        raise IntegrationMalformedResponse(
            "Discord human-interrupt delivery requires the discord/webhook outbound-event-sink binding"
        )
    _discord_settings(binding.settings)
    return DiscordHumanInterruptSink(integration_id=binding.integration_id, transport=transport)


def discord_human_interrupt_sink_registration() -> AdapterRegistration:
    """Return the narrow outbound-only Discord adapter registration."""

    return AdapterRegistration(
        provider="discord",
        transport=TransportKind.WEBHOOK,
        capability=CapabilitySurface.OUTBOUND_EVENT_SINK,
        required_transport_operations=frozenset({TransportOperation.WEBHOOK_DELIVER}),
        factory=discord_human_interrupt_sink_factory,
    )


def _discord_settings(settings: Mapping[str, Any]) -> tuple[str, float]:
    allowed = {"webhook_id", "timeout_seconds"}
    unknown = set(settings) - allowed
    if unknown:
        raise IntegrationConfigurationError(
            "Discord webhook settings contain unsupported keys: " + ", ".join(sorted(unknown))
        )
    webhook_id = settings.get("webhook_id")
    if not isinstance(webhook_id, str) or not _DISCORD_WEBHOOK_ID.fullmatch(webhook_id):
        raise IntegrationConfigurationError("Discord webhook settings require a numeric webhook_id")
    timeout = settings.get("timeout_seconds", 10.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise IntegrationConfigurationError("Discord webhook timeout_seconds must be a number")
    timeout_seconds = float(timeout)
    if not 0 < timeout_seconds <= 60:
        raise IntegrationConfigurationError(
            "Discord webhook timeout_seconds must be greater than 0 and at most 60"
        )
    return webhook_id, timeout_seconds


def _discord_payload(event: OutboundEvent) -> dict[str, object]:
    """Render safe, bounded content without exposing interrupt context."""

    data = event.data
    work_item_id = event.provenance.work_item_id
    if (
        not isinstance(data, HumanInterruptData)
        or data.phase is None
        or work_item_id is None
    ):
        raise IntegrationMalformedResponse(
            "Discord human-interrupt delivery requires work_item_id and phase"
        )
    run_id = _quoted(event.provenance.run_id)
    content = "\n".join(
        (
            "Battalion requires human review.",
            f"Run: {_quoted(event.provenance.run_id)}",
            f"Work item: {_quoted(work_item_id)}",
            f"Phase: {_quoted(data.phase)}",
            f"Reason: {_quoted(data.trigger)}",
            f"Return to Battalion: battalion status {run_id} --human",
        )
    )
    if len(content) > 2000:  # Defensive guard against future schema growth.
        raise IntegrationMalformedResponse("Discord human-interrupt content exceeds Discord's limit")
    return {"content": content, "allowed_mentions": {"parse": []}}


def _quoted(value: str) -> str:
    """Use JSON string escaping so state values cannot format Discord markup."""

    return json.dumps(value, ensure_ascii=False)
