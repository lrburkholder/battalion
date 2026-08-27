"""Deterministic tests for the BTN-67 integration adapter runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from battalion.integrations.configuration import (
    CapabilitySurface,
    IntegrationConfiguration,
    TransportKind,
)
from battalion.integrations.runtime import (
    AdapterBinding,
    AdapterRegistration,
    BoundTransport,
    CapabilityNotConfigured,
    IntegrationCancelled,
    IntegrationMalformedResponse,
    IntegrationNotConfigured,
    IntegrationRuntime,
    IntegrationTimeout,
    IntegrationTransportFailure,
    IntegrationUnavailable,
    TransportCall,
    TransportOperation,
    TransportOperationDenied,
    TransportResponse,
    UnsupportedProviderTransport,
    UnsupportedTransportOperation,
)
from battalion.work import WorkItem, WorkItemProvenance


def _configuration(
    *,
    provider: str = "fake-work",
    transport: str = "native-local",
    capabilities: list[str] | None = None,
) -> IntegrationConfiguration:
    return IntegrationConfiguration.model_validate(
        {
            "project": {
                "integrations": {
                    "work": {
                        "integration_id": "work-primary",
                        "provider": provider,
                        "transport": transport,
                        "capabilities": capabilities or ["work-source"],
                        "settings": {"path": "work-items.json", "nested": {"ids": [1]}},
                        "credential_references": {
                            "token": {"reference": "env://WORK_TOKEN"}
                        },
                    }
                }
            }
        }
    )


@dataclass
class FakeTransport:
    response: object = field(default_factory=lambda: TransportResponse({"id": "one"}))
    failure: BaseException | None = None
    calls: list[tuple[TransportOperation, TransportCall]] = field(default_factory=list)

    def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
        self.calls.append((operation, call))
        if self.failure is not None:
            raise self.failure
        return self.response  # type: ignore[return-value]


@dataclass
class FakeWorkSource:
    capability: CapabilitySurface
    _transport: BoundTransport
    integration_id: str = "work-primary"

    def get(self, item_id: str) -> WorkItem:
        response = self._transport.invoke(
            TransportOperation.NATIVE_LOCAL_READ, TransportCall({"item_id": item_id})
        )
        return WorkItem(
            source_integration_id=self.integration_id,
            external_id=item_id,
            title=str(response.payload["id"]),
            provenance=WorkItemProvenance(
                retrieved_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
                operation="work.get",
            ),
        )

    def refresh(self, item: WorkItem) -> WorkItem:
        return self.get(item.external_id)


class FakeAdapterFactory:
    def __init__(self, capability: CapabilitySurface = CapabilitySurface.WORK_SOURCE) -> None:
        self.capability = capability
        self.bindings: list[AdapterBinding] = []
        self.transports: list[BoundTransport] = []
        self.failure: Exception | None = None

    def __call__(self, binding: AdapterBinding, transport: BoundTransport) -> FakeWorkSource:
        self.bindings.append(binding)
        self.transports.append(transport)
        if self.failure is not None:
            raise self.failure
        return FakeWorkSource(self.capability, transport)


def _registration(factory: FakeAdapterFactory) -> AdapterRegistration:
    return AdapterRegistration(
        provider="fake-work",
        transport=TransportKind.NATIVE_LOCAL,
        capability=CapabilitySurface.WORK_SOURCE,
        required_transport_operations=frozenset({TransportOperation.NATIVE_LOCAL_READ}),
        factory=factory,
    )


def _runtime(
    transport: FakeTransport | None = None,
    factory: FakeAdapterFactory | None = None,
) -> tuple[IntegrationRuntime, FakeTransport, FakeAdapterFactory]:
    actual_transport = transport or FakeTransport()
    actual_factory = factory or FakeAdapterFactory()
    runtime = IntegrationRuntime(
        _configuration(),
        adapters=(_registration(actual_factory),),
        transports={TransportKind.NATIVE_LOCAL: lambda binding: actual_transport},
    )
    return runtime, actual_transport, actual_factory


def test_runtime_resolves_a_capability_specific_port_through_configured_binding():
    runtime, transport, factory = _runtime()

    port = runtime.work_source("work")

    assert port.integration_id == "work-primary"
    assert port.get("one").external_id == "one"
    assert not hasattr(port, "comment")
    assert transport.calls == [
        (TransportOperation.NATIVE_LOCAL_READ, TransportCall({"item_id": "one"}))
    ]
    assert factory.bindings[0].integration_id == "work-primary"
    assert factory.bindings[0].settings["nested"]["ids"] == (1,)
    with pytest.raises(TypeError):
        factory.bindings[0].settings["path"] = "other.json"  # type: ignore[index]


def test_adapter_receives_only_its_declared_transport_mechanics():
    runtime, _, factory = _runtime()

    runtime.work_source("work")

    bound_transport = factory.transports[0]
    assert bound_transport.allowed_operations == frozenset(
        {TransportOperation.NATIVE_LOCAL_READ}
    )
    assert not hasattr(bound_transport, "transport")
    with pytest.raises(TransportOperationDenied):
        bound_transport.invoke(
            TransportOperation.HTTP_REQUEST, TransportCall({"method": "GET"})
        )


def test_unsupported_provider_transport_combination_fails_before_resolution():
    factory = FakeAdapterFactory()

    with pytest.raises(UnsupportedProviderTransport, match="no approved adapter"):
        IntegrationRuntime(
            _configuration(transport="http-rest"),
            adapters=(_registration(factory),),
            transports={TransportKind.HTTP_REST: lambda binding: FakeTransport()},
        )


def test_missing_transport_fails_validation_before_run_execution():
    with pytest.raises(UnsupportedProviderTransport, match="unregistered transport"):
        IntegrationRuntime(_configuration(), adapters=(_registration(FakeAdapterFactory()),))


def test_adapter_cannot_request_transport_operation_from_another_transport_kind():
    registration = AdapterRegistration(
        provider="fake-work",
        transport=TransportKind.NATIVE_LOCAL,
        capability=CapabilitySurface.WORK_SOURCE,
        required_transport_operations=frozenset({TransportOperation.HTTP_REQUEST}),
        factory=FakeAdapterFactory(),
    )

    with pytest.raises(UnsupportedTransportOperation, match="cannot provide"):
        IntegrationRuntime(
            _configuration(),
            adapters=(registration,),
            transports={TransportKind.NATIVE_LOCAL: lambda binding: FakeTransport()},
        )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError(), IntegrationTimeout),
        (asyncio.CancelledError(), IntegrationCancelled),
        (OSError(), IntegrationTransportFailure),
        (RuntimeError("connection dropped"), IntegrationTransportFailure),
    ],
)
def test_transport_failures_are_typed_and_do_not_escape_as_provider_exceptions(
    failure: BaseException, expected: type[Exception]
):
    runtime, _, _ = _runtime(FakeTransport(failure=failure))
    port = runtime.work_source("work")

    with pytest.raises(expected):
        port.get("one")


def test_malformed_transport_response_has_typed_failure_semantics():
    runtime, _, _ = _runtime(FakeTransport(response={"unbounded": "response"}))
    port = runtime.work_source("work")

    with pytest.raises(IntegrationMalformedResponse, match="TransportResponse"):
        port.get("one")


def test_adapter_construction_failure_is_typed_unavailable_integration():
    factory = FakeAdapterFactory()
    factory.failure = ValueError("bad provider setup")
    runtime, _, _ = _runtime(factory=factory)

    with pytest.raises(IntegrationUnavailable, match="could not be constructed"):
        runtime.work_source("work")


def test_adapter_must_return_the_capability_it_was_bound_to():
    runtime, _, _ = _runtime(factory=FakeAdapterFactory(CapabilitySurface.NOTIFICATION))

    with pytest.raises(IntegrationMalformedResponse, match="work-source"):
        runtime.work_source("work")


def test_resolution_rejects_unknown_integration_and_unconfigured_capability():
    runtime, _, _ = _runtime()

    with pytest.raises(IntegrationNotConfigured):
        runtime.work_source("missing")
    with pytest.raises(CapabilityNotConfigured):
        runtime.notification("work")
