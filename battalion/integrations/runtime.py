"""Configured, least-authority integration runtime (BTN-67).

The runtime sits below Battalion application policy and above provider-specific
transports.  It deliberately resolves only configured capability bindings:
application code receives capability-specific ports, provider adapters receive
only a bounded transport facade, and neither layer receives a raw client
registry.

Individual capability operations and their policy semantics are delivered by
later tickets.  This module establishes the structural binding needed for
those operations without granting a generic external-tool API.  Durable
side-effect evidence and replay-safe delivery for externally visible
operations live in :mod:`battalion.integrations.effects` (BTN-70).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, cast

from battalion.integrations.configuration import (
    CapabilitySurface,
    IntegrationConfiguration,
    IntegrationDefinition,
    SecretReference,
    TransportKind,
)
from battalion.work import WorkItem

if TYPE_CHECKING:
    from battalion.integrations.events import ConfiguredOutboundEventSink, OutboundEvent


class IntegrationError(Exception):
    """Base class for expected capability/provider/transport failures."""


class IntegrationConfigurationError(IntegrationError):
    """Configured integrations cannot be bound safely before run execution."""


class UnsupportedProviderTransport(IntegrationConfigurationError):
    """No approved adapter supports a configured provider/transport/capability."""


class UnsupportedTransportOperation(IntegrationConfigurationError):
    """An adapter requested a transport mechanic its transport kind cannot provide."""


class IntegrationNotConfigured(IntegrationError):
    """No configured integration provides the requested Battalion capability."""


class CapabilityNotConfigured(IntegrationError):
    """A selected integration does not expose the requested capability."""


class AmbiguousIntegration(IntegrationError):
    """More than one configured integration could satisfy a capability request."""


class IntegrationPolicyDenied(IntegrationError):
    """The configured organization ceiling excludes an integration binding."""


class IntegrationUnavailable(IntegrationError):
    """An approved integration cannot currently be constructed or reached."""


class TransportOperationDenied(IntegrationError):
    """An adapter attempted a transport operation it was not explicitly granted."""


class IntegrationTimeout(IntegrationError):
    """A bounded integration call timed out."""


class IntegrationCancelled(IntegrationError):
    """A bounded integration call was explicitly cancelled."""


class IntegrationTransportFailure(IntegrationError):
    """A transport or connectivity failure prevented an integration call."""


class IntegrationMalformedResponse(IntegrationError):
    """A transport or adapter returned data outside its declared boundary."""


class TransportOperation(str, Enum):
    """The finite mechanics that can be granted to a provider adapter.

    These are transport mechanics, not Battalion capability operations.  A
    provider adapter only receives the subset declared in its registration.
    """

    NATIVE_LOCAL_READ = "native-local.read"
    HTTP_REQUEST = "http-rest.request"
    WEBHOOK_DELIVER = "webhook.deliver"
    MCP_INVOKE = "mcp.invoke"
    PROTOCOL_DELIVER = "protocol-specific.deliver"


_TRANSPORT_OPERATIONS: Mapping[TransportKind, frozenset[TransportOperation]] = {
    TransportKind.NATIVE_LOCAL: frozenset({TransportOperation.NATIVE_LOCAL_READ}),
    TransportKind.HTTP_REST: frozenset({TransportOperation.HTTP_REQUEST}),
    TransportKind.WEBHOOK: frozenset({TransportOperation.WEBHOOK_DELIVER}),
    TransportKind.MCP: frozenset({TransportOperation.MCP_INVOKE}),
    TransportKind.PROTOCOL_SPECIFIC: frozenset({TransportOperation.PROTOCOL_DELIVER}),
}


@dataclass(frozen=True)
class AdapterBinding:
    """The non-secret configuration slice admitted to one provider adapter."""

    integration_id: str
    provider: str
    transport: TransportKind
    capability: CapabilitySurface
    settings: Mapping[str, Any]
    credential_references: Mapping[str, SecretReference]


@dataclass(frozen=True)
class TransportCall:
    """Opaque provider-facing input that remains below the capability boundary."""

    payload: Any


@dataclass(frozen=True)
class TransportResponse:
    """Opaque provider-facing output for adapter normalization only."""

    payload: Any


class Transport(Protocol):
    """A transport implementation hidden behind an adapter-specific facade."""

    def invoke(
        self, operation: TransportOperation, call: TransportCall
    ) -> TransportResponse:
        """Perform one bounded transport mechanic."""


class CapabilityPort(Protocol):
    """A marker for a provider-neutral, normalized Battalion capability port."""

    @property
    def capability(self) -> CapabilitySurface:
        """The one capability surface implemented by this port."""


class WorkSourcePort(CapabilityPort, Protocol):
    """Provider-neutral, read-only WorkSource port used for Run intake."""

    @property
    def integration_id(self) -> str:
        """Stable configured integration identity for retrieved work."""

    def get(self, external_id: str) -> "WorkItem":
        """Return one normalized work-item snapshot."""

    def refresh(self, item: "WorkItem") -> "WorkItem":
        """Return a refreshed normalized work-item snapshot."""


class _ReadOnlyWorkSource:
    """Capability wrapper that excludes provider-specific mutation methods."""

    __slots__ = ("__get", "__integration_id", "__refresh")

    def __init__(self, source: WorkSourcePort) -> None:
        self.__integration_id = source.integration_id
        self.__get = source.get
        self.__refresh = source.refresh

    @property
    def capability(self) -> CapabilitySurface:
        return CapabilitySurface.WORK_SOURCE

    @property
    def integration_id(self) -> str:
        return self.__integration_id

    def get(self, external_id: str) -> WorkItem:
        return self._validate_item(self.__get(external_id), "work.get")

    def refresh(self, item: WorkItem) -> WorkItem:
        return self._validate_item(self.__refresh(item), "work.refresh")

    @staticmethod
    def _validate_item(item: object, operation: str) -> WorkItem:
        if not isinstance(item, WorkItem):
            raise IntegrationMalformedResponse(
                f"{operation} returned a value outside the WorkItem contract"
            )
        return item


class KnowledgeSourcePort(CapabilityPort, Protocol):
    """Provider-neutral KnowledgeSource port; operations arrive with BTN-73."""


class RepositoryServicePort(CapabilityPort, Protocol):
    """Provider-neutral RepositoryService port; operations arrive with BTN-74."""


class NotificationPort(CapabilityPort, Protocol):
    """Provider-neutral Notification port; operations arrive with BTN-75."""


class OutboundEventSinkPort(CapabilityPort, Protocol):
    """One-way, provider-neutral machine-event publication port (BTN-73)."""

    def publish(self, event: "OutboundEvent", *, idempotency_key: str) -> Any:
        """Publish one versioned event using Battalion's stable operation ID.

        The port deliberately has no command, actor, Run, or state access.
        Provider adapters return only bounded delivery evidence; the application
        retains policy and durable side-effect coordination.
        """


class _ConfiguredOutboundEventSink:
    """Expose one-way publication plus safe binding evidence, not an adapter."""

    __slots__ = (
        "__integration_id",
        "__integration_name",
        "__provider",
        "__accepts",
        "__publish",
        "__transport",
    )

    def __init__(
        self,
        source: OutboundEventSinkPort,
        *,
        integration_name: str,
        definition: IntegrationDefinition,
    ) -> None:
        try:
            publish = source.publish
        except AttributeError as exc:
            raise IntegrationMalformedResponse(
                "outbound-event-sink adapter must provide publish"
            ) from exc
        if not callable(publish):
            raise IntegrationMalformedResponse(
                "outbound-event-sink publish must be callable"
            )
        accepts = getattr(source, "accepts", lambda event: True)
        if not callable(accepts):
            raise IntegrationMalformedResponse("outbound-event-sink accepts must be callable")
        self.__integration_id = definition.integration_id
        self.__integration_name = integration_name
        self.__provider = definition.provider
        self.__accepts = accepts
        self.__publish = publish
        self.__transport = definition.transport

    @property
    def capability(self) -> CapabilitySurface:
        return CapabilitySurface.OUTBOUND_EVENT_SINK

    @property
    def integration_id(self) -> str:
        return self.__integration_id

    @property
    def integration_name(self) -> str:
        return self.__integration_name

    @property
    def provider(self) -> str:
        return self.__provider

    @property
    def transport(self) -> TransportKind:
        return self.__transport

    def accepts(self, event: "OutboundEvent") -> bool:
        accepted = self.__accepts(event)
        if not isinstance(accepted, bool):
            raise IntegrationMalformedResponse("outbound-event-sink accepts must return a boolean")
        return accepted

    def publish(self, event: "OutboundEvent", *, idempotency_key: str) -> Any:
        return self.__publish(event, idempotency_key=idempotency_key)


class HumanInteractionPort(CapabilityPort, Protocol):
    """Provider-neutral HumanInteraction port; operations arrive with BTN-77."""


class BoundTransport:
    """Only the mechanics explicitly granted to one provider adapter.

    A raw ``Transport`` never leaves this wrapper.  It also converts ordinary
    bounded-call failures to the common typed integration failure vocabulary.
    """

    __slots__ = ("__allowed_operations", "__invoke")

    def __init__(
        self,
        transport: Transport,
        allowed_operations: frozenset[TransportOperation],
    ) -> None:
        self.__allowed_operations = allowed_operations
        self.__invoke: Callable[[TransportOperation, TransportCall], TransportResponse] = (
            transport.invoke
        )

    @property
    def allowed_operations(self) -> frozenset[TransportOperation]:
        """Declared mechanics, exposed for deterministic adapter validation."""

        return self.__allowed_operations

    def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
        """Invoke an explicitly bound mechanic and normalize its failure shape."""

        if operation not in self.__allowed_operations:
            raise TransportOperationDenied(
                f"Transport operation {operation.value!r} is not bound to this adapter"
            )
        if not isinstance(call, TransportCall):
            raise IntegrationMalformedResponse("transport calls must use TransportCall")

        try:
            response = self.__invoke(operation, call)
        except IntegrationError:
            raise
        except asyncio.CancelledError as exc:
            raise IntegrationCancelled("integration call was cancelled") from exc
        except TimeoutError as exc:
            raise IntegrationTimeout("integration call timed out") from exc
        except OSError as exc:
            raise IntegrationTransportFailure("integration transport failed") from exc
        except Exception as exc:
            raise IntegrationTransportFailure("integration transport failed") from exc

        if not isinstance(response, TransportResponse):
            raise IntegrationMalformedResponse(
                "transport returned a value outside TransportResponse"
            )
        return response


class TransportFactory(Protocol):
    """Constructs one bounded transport from one admitted integration binding."""

    def __call__(self, binding: AdapterBinding) -> Transport:
        """Return a transport implementation for a single integration."""


class ProviderAdapterFactory(Protocol):
    """Constructs one provider adapter using no raw transport client."""

    def __call__(self, binding: AdapterBinding, transport: BoundTransport) -> CapabilityPort:
        """Return a normalized capability port for one configured binding."""


@dataclass(frozen=True)
class AdapterRegistration:
    """Approved capability-to-provider-to-transport binding metadata."""

    provider: str
    transport: TransportKind
    capability: CapabilitySurface
    required_transport_operations: frozenset[TransportOperation]
    factory: ProviderAdapterFactory


class IntegrationRuntime:
    """Resolve portable configuration through registered, explicit bindings.

    Construction validates every configured capability before any Run begins.
    Resolution returns a capability-specific port rather than a provider client,
    adapter registry, credential resolver, or generic transport handle.
    """

    def __init__(
        self,
        configuration: IntegrationConfiguration,
        *,
        adapters: tuple[AdapterRegistration, ...] = (),
        transports: Mapping[TransportKind, TransportFactory] | None = None,
    ) -> None:
        self._configuration = configuration
        self._transports = dict(transports or {})
        self._adapters: dict[
            tuple[str, TransportKind, CapabilitySurface], AdapterRegistration
        ] = {}

        for adapter in adapters:
            key = (adapter.provider, adapter.transport, adapter.capability)
            if key in self._adapters:
                raise IntegrationConfigurationError(
                    "duplicate provider adapter registration for "
                    f"{adapter.provider!r}, {adapter.transport.value!r}, "
                    f"and {adapter.capability.value!r}"
                )
            self._validate_transport_operations(adapter)
            self._adapters[key] = adapter

        self.validate()

    def validate(self) -> None:
        """Fail early for unsupported configured provider/transport combinations."""

        for name, definition in self._configuration.project.integrations.items():
            if definition.transport not in self._transports:
                raise UnsupportedProviderTransport(
                    f"Integration {name!r} requires unregistered transport "
                    f"{definition.transport.value!r}"
                )
            for capability in definition.capabilities:
                key = (definition.provider, definition.transport, capability)
                if key not in self._adapters:
                    raise UnsupportedProviderTransport(
                        f"Integration {name!r} has no approved adapter for provider "
                        f"{definition.provider!r}, transport {definition.transport.value!r}, "
                        f"and capability {capability.value!r}"
                    )

    def work_source(self, integration_name: str | None = None) -> WorkSourcePort:
        """Resolve an admitted WorkSource port."""

        return _ReadOnlyWorkSource(
            cast(WorkSourcePort, self._resolve(CapabilitySurface.WORK_SOURCE, integration_name))
        )

    def knowledge_source(self, integration_name: str | None = None) -> KnowledgeSourcePort:
        """Resolve an admitted KnowledgeSource port."""

        return cast(
            KnowledgeSourcePort,
            self._resolve(CapabilitySurface.KNOWLEDGE_SOURCE, integration_name),
        )

    def repository_service(
        self, integration_name: str | None = None
    ) -> RepositoryServicePort:
        """Resolve an admitted RepositoryService port."""

        return cast(
            RepositoryServicePort,
            self._resolve(CapabilitySurface.REPOSITORY_SERVICE, integration_name),
        )

    def notification(self, integration_name: str | None = None) -> NotificationPort:
        """Resolve an admitted Notification port."""

        return cast(
            NotificationPort,
            self._resolve(CapabilitySurface.NOTIFICATION, integration_name),
        )

    def outbound_event_sink(
        self, integration_name: str | None = None
    ) -> "ConfiguredOutboundEventSink":
        """Resolve an admitted OutboundEventSink port."""
        name, definition = self._select_definition(
            CapabilitySurface.OUTBOUND_EVENT_SINK, integration_name
        )
        source = cast(
            OutboundEventSinkPort,
            self._resolve(CapabilitySurface.OUTBOUND_EVENT_SINK, name),
        )
        return _ConfiguredOutboundEventSink(
            source, integration_name=name, definition=definition
        )

    def outbound_event_sinks(self) -> tuple["ConfiguredOutboundEventSink", ...]:
        """Resolve every configured, policy-permitted machine event sink.

        Outbound events intentionally fan out to selected configured providers.
        They are not narrowed to a human Actor's preferred destination.
        """

        return tuple(
            self.outbound_event_sink(name)
            for name, definition in self._configuration.project.integrations.items()
            if CapabilitySurface.OUTBOUND_EVENT_SINK in definition.capabilities
            and self._is_allowed(name)
        )

    def human_interaction(
        self, integration_name: str | None = None
    ) -> HumanInteractionPort:
        """Resolve an admitted HumanInteraction port."""

        return cast(
            HumanInteractionPort,
            self._resolve(CapabilitySurface.HUMAN_INTERACTION, integration_name),
        )

    def _resolve(
        self, capability: CapabilitySurface, integration_name: str | None
    ) -> CapabilityPort:
        name, definition = self._select_definition(capability, integration_name)
        registration = self._adapters[(definition.provider, definition.transport, capability)]
        binding = _adapter_binding(definition, capability)

        try:
            transport = self._transports[definition.transport](binding)
            if not hasattr(transport, "invoke"):
                raise IntegrationMalformedResponse("transport factory returned no transport")
            adapter = registration.factory(
                binding,
                BoundTransport(transport, registration.required_transport_operations),
            )
        except IntegrationError:
            raise
        except Exception as exc:
            raise IntegrationUnavailable(
                f"Integration {name!r} could not be constructed"
            ) from exc

        if not _has_capability(adapter, capability):
            raise IntegrationMalformedResponse(
                f"Adapter for integration {name!r} did not return "
                f"the {capability.value!r} capability port"
            )
        return adapter

    def _select_definition(
        self, capability: CapabilitySurface, integration_name: str | None
    ) -> tuple[str, IntegrationDefinition]:
        integrations = self._configuration.project.integrations
        if integration_name is not None:
            definition = integrations.get(integration_name)
            if definition is None:
                raise IntegrationNotConfigured(
                    f"No integration named {integration_name!r} is configured"
                )
            self._ensure_allowed(integration_name)
            if capability not in definition.capabilities:
                raise CapabilityNotConfigured(
                    f"Integration {integration_name!r} does not provide "
                    f"{capability.value!r}"
                )
            return integration_name, definition

        candidates = [
            (name, definition)
            for name, definition in integrations.items()
            if capability in definition.capabilities and self._is_allowed(name)
        ]
        if not candidates:
            raise IntegrationNotConfigured(
                f"No allowed integration provides {capability.value!r}"
            )
        if len(candidates) > 1:
            raise AmbiguousIntegration(
                f"More than one integration provides {capability.value!r}; "
                "select an integration by name"
            )
        return candidates[0]

    def _ensure_allowed(self, integration_name: str) -> None:
        if not self._is_allowed(integration_name):
            raise IntegrationPolicyDenied(
                f"Integration {integration_name!r} is excluded by organization policy"
            )

    def _is_allowed(self, integration_name: str) -> bool:
        organization = self._configuration.organization
        return (
            organization is None
            or organization.allowed_integrations is None
            or integration_name in organization.allowed_integrations
        )

    @staticmethod
    def _validate_transport_operations(adapter: AdapterRegistration) -> None:
        supported = _TRANSPORT_OPERATIONS[adapter.transport]
        unsupported = adapter.required_transport_operations - supported
        if unsupported:
            names = ", ".join(sorted(operation.value for operation in unsupported))
            raise UnsupportedTransportOperation(
                f"Adapter {adapter.provider!r} requests {names}, which "
                f"{adapter.transport.value!r} cannot provide"
            )


def _adapter_binding(
    definition: IntegrationDefinition, capability: CapabilitySurface
) -> AdapterBinding:
    """Create immutable, one-integration configuration for an adapter factory."""

    return AdapterBinding(
        integration_id=definition.integration_id,
        provider=definition.provider,
        transport=definition.transport,
        capability=capability,
        settings=_freeze_mapping(definition.settings),
        credential_references=MappingProxyType(dict(definition.credential_references)),
    )


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prevent an adapter from changing portable configuration by reference."""

    return MappingProxyType({key: _freeze(value) for key, value in values.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _has_capability(adapter: object, capability: CapabilitySurface) -> bool:
    try:
        if getattr(adapter, "capability") is not capability:
            return False
        if capability is CapabilitySurface.WORK_SOURCE:
            return (
                isinstance(getattr(adapter, "integration_id"), str)
                and callable(getattr(adapter, "get"))
                and callable(getattr(adapter, "refresh"))
            )
        return True
    except Exception:
        return False
