"""Battalion-owned external-integration configuration contracts."""

from battalion.integrations.configuration import (
    ActorIntegrationPreferences,
    CapabilitySurface,
    IntegrationConfiguration,
    IntegrationDefinition,
    IntegrationLayer,
    OrganizationIntegrationPolicy,
    SecretReference,
    TransportKind,
    export_portable_integrations,
)

__all__ = [
    "ActorIntegrationPreferences",
    "CapabilitySurface",
    "IntegrationConfiguration",
    "IntegrationDefinition",
    "IntegrationLayer",
    "OrganizationIntegrationPolicy",
    "SecretReference",
    "TransportKind",
    "export_portable_integrations",
]
