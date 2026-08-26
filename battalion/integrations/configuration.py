"""Portable configuration models for Battalion-owned integrations.

This module deliberately describes configuration only.  BTN-67's runtime
binds provider adapters and transports beneath these models; health checks,
credential resolution, and operation policy remain separate follow-up
concerns.  Keeping the models here prevents shareable project configuration
from becoming a source of ambient provider authority.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from battalion.config import BattalionConfig


class CapabilitySurface(str, Enum):
    """The provider-neutral capability surfaces accepted in RFC-0006."""

    WORK_SOURCE = "work-source"
    KNOWLEDGE_SOURCE = "knowledge-source"
    REPOSITORY_SERVICE = "repository-service"
    NOTIFICATION = "notification"
    OUTBOUND_EVENT_SINK = "outbound-event-sink"
    HUMAN_INTERACTION = "human-interaction"


class TransportKind(str, Enum):
    """Transport categories; these are not provider-specific client types."""

    NATIVE_LOCAL = "native-local"
    HTTP_REST = "http-rest"
    WEBHOOK = "webhook"
    MCP = "mcp"
    PROTOCOL_SPECIFIC = "protocol-specific"


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ENV_REFERENCE = re.compile(r"^env://[A-Z_][A-Z0-9_]*$")
_KEYRING_REFERENCE = re.compile(r"^keyring://[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")
_SENSITIVE_SETTING_NAMES = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "apisecret",
        "apitoken",
        "authorization",
        "bearertoken",
        "clientsecret",
        "credential",
        "password",
        "privatekey",
        "secret",
        "token",
    }
)


def _normalise_setting_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _reject_secret_settings(value: Any, path: str = "settings") -> None:
    """Reject conventional secret fields in the portable settings tree.

    A string's contents cannot reliably prove whether it is secret material,
    so the boundary is structural: values with a secret-bearing field name must
    use ``credential_references`` instead.  Recursive checking prevents a
    nested provider settings object from bypassing the same rule.
    """

    if isinstance(value, dict):
        for key, nested in value.items():
            name = str(key)
            if _normalise_setting_name(name) in _SENSITIVE_SETTING_NAMES:
                raise ValueError(
                    f"{path}.{name} may contain secret material; use "
                    "credential_references instead"
                )
            _reject_secret_settings(nested, f"{path}.{name}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_settings(nested, f"{path}[{index}]")


class SecretReference(BaseModel):
    """A symbolic secret location, never a credential value.

    ``env://NAME`` is suitable for local or CI environment injection.  The
    ``keyring://service/account`` form reserves a stable non-file-based lookup
    for an operating-system or organization credential store.  Resolution is
    intentionally outside this configuration model and is not implemented by
    BTN-66.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    reference: str = Field(min_length=7, max_length=256)

    @field_validator("reference")
    @classmethod
    def validate_approved_reference(cls, reference: str) -> str:
        if not (_ENV_REFERENCE.fullmatch(reference) or _KEYRING_REFERENCE.fullmatch(reference)):
            raise ValueError(
                "reference must use an approved secret reference scheme: "
                "env://NAME or keyring://service/account"
            )
        return reference


class IntegrationDefinition(BaseModel):
    """One stable configured provider binding owned by a Battalion project."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    integration_id: str = Field(min_length=1, max_length=63)
    provider: str = Field(min_length=1, max_length=63)
    transport: TransportKind
    capabilities: frozenset[CapabilitySurface] = Field(min_length=1)
    settings: dict[str, Any] = Field(default_factory=dict)
    credential_references: dict[str, SecretReference] = Field(default_factory=dict)

    @field_validator("integration_id", "provider")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(
                "must be a stable lowercase identifier using letters, digits, "
                "and hyphens"
            )
        return value

    @field_validator("settings")
    @classmethod
    def validate_portable_settings(cls, settings: dict[str, Any]) -> dict[str, Any]:
        _reject_secret_settings(settings)
        return settings


class IntegrationLayer(BaseModel):
    """Named project bindings, intentionally separate from a provider name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    integrations: dict[str, IntegrationDefinition] = Field(default_factory=dict)

    @field_validator("integrations")
    @classmethod
    def validate_names_and_stable_ids(
        cls, integrations: dict[str, IntegrationDefinition]
    ) -> dict[str, IntegrationDefinition]:
        integration_ids: set[str] = set()
        for name, definition in integrations.items():
            if not _IDENTIFIER.fullmatch(name):
                raise ValueError(
                    f"integration name {name!r} must be a lowercase stable identifier"
                )
            if definition.integration_id in integration_ids:
                raise ValueError(
                    f"integration_id {definition.integration_id!r} must be unique"
                )
            integration_ids.add(definition.integration_id)
        return integrations


class OrganizationIntegrationPolicy(BaseModel):
    """A future organization ceiling that can only narrow project choices."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_integrations: frozenset[str] | None = None


class ActorIntegrationPreferences(BaseModel):
    """Future Actor selection hints constrained by the project binding layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preferred_integrations: dict[CapabilitySurface, str] = Field(default_factory=dict)


class IntegrationConfiguration(BaseModel):
    """Portable integration configuration with explicit precedence boundaries.

    BTN-66 configures project bindings.  Organization policy and Actor
    preferences are represented only as restrictive/selection layers, so later
    delivery can add their administration without changing the portable shape.
    They do not establish runtime authorization, credentials, or providers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    organization: OrganizationIntegrationPolicy | None = None
    project: IntegrationLayer = Field(default_factory=IntegrationLayer)
    actor_preferences: dict[str, ActorIntegrationPreferences] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_precedence(self) -> "IntegrationConfiguration":
        integrations = self.project.integrations
        allowed = set(integrations)

        if self.organization and self.organization.allowed_integrations is not None:
            unknown = self.organization.allowed_integrations - set(integrations)
            if unknown:
                raise ValueError(
                    "organization policy names integrations absent from the project: "
                    + ", ".join(sorted(unknown))
                )
            allowed &= self.organization.allowed_integrations

        for actor_id, preferences in self.actor_preferences.items():
            for capability, integration_name in preferences.preferred_integrations.items():
                if integration_name not in allowed:
                    raise ValueError(
                        f"Actor {actor_id!r} preference {integration_name!r} is not "
                        "permitted by project policy"
                    )
                if capability not in integrations[integration_name].capabilities:
                    raise ValueError(
                        f"Actor {actor_id!r} preference {integration_name!r} does not "
                        f"provide capability {capability.value!r}"
                    )
        return self


def export_portable_integrations(config: "BattalionConfig") -> dict[str, Any]:
    """Return the shareable integration portion of a Battalion configuration.

    The returned data includes only settings and symbolic secret references.
    Actual secret resolution is outside the portable configuration contract, so
    credential material cannot enter an export through this API.
    """

    return config.integrations.model_dump(mode="json")
