"""Project-local durable Actor identity and first-use bootstrap persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from battalion.identity import load_project_identity


ACTOR_REGISTRY = Path(".battalion/actors.json")
BOOTSTRAP_CAPABILITIES = (
    "inspect",
    "operate",
    "resolve-interrupt",
    "approve",
    "assign",
    "administer",
)
_INTEGRATION_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SENSITIVE_METADATA_NAMES = frozenset(
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


class ActorError(ValueError):
    """Base class for durable Actor contract and persistence failures."""


class MalformedActorRegistry(ActorError):
    """The project Actor registry exists but cannot be trusted."""


class ActorBootstrapConsumed(ActorError):
    """The one-time project trust-root ceremony has already occurred."""


class ActorNotFound(ActorError):
    """No Actor with the requested durable identifier exists."""


class ActorUnavailable(ActorError):
    """An Actor exists but cannot be selected for a new action."""


class ExternalIdentityNotFound(ActorError):
    """No external identity mapping matches the supplied provider subject."""


class ExternalIdentityAlreadyLinked(ActorError):
    """An integration-scoped external subject is already linked to an Actor."""


class ActorKind(str, Enum):
    HUMAN = "human"
    SYSTEM = "system"


class ActorStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ActorCreationProvenance(str, Enum):
    FIRST_PROJECT_BOOTSTRAP = "first-project-bootstrap"
    LOCAL_ADMINISTRATION = "local-administration"
    SYSTEM_PROVISIONING = "system-provisioning"


class _ActorContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class Actor(_ActorContract):
    """Battalion-owned identity; names and provider subjects are not identity."""

    schema_version: Literal["1.0"] = "1.0"
    actor_id: UUID
    kind: ActorKind
    display_name: str = Field(min_length=1, max_length=200)
    status: ActorStatus = ActorStatus.ACTIVE
    created_at: datetime
    created_by: UUID | None
    provenance: ActorCreationProvenance

    @model_validator(mode="after")
    def validate_creation(self) -> Self:
        if self.created_at.utcoffset() is None:
            raise ValueError("Actor creation time must be timezone-aware")
        bootstrap = self.provenance is ActorCreationProvenance.FIRST_PROJECT_BOOTSTRAP
        if bootstrap:
            if self.kind is not ActorKind.HUMAN or self.created_by is not None:
                raise ValueError("the first bootstrap Actor must be a self-created human")
        elif self.created_by is None:
            raise ValueError("non-bootstrap Actors require a creating Actor reference")
        return self


def _normalise_metadata_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _reject_secret_metadata(value: JsonValue, path: str = "metadata") -> None:
    """Reject conventional credential-bearing fields from durable metadata.

    Metadata supports provider context needed to display or diagnose a binding,
    not authentication material.  As with portable integration configuration,
    this is a structural guard: secret-bearing field names must never enter the
    durable Actor registry.
    """

    if isinstance(value, dict):
        for key, nested in value.items():
            name = str(key)
            if _normalise_metadata_name(name) in _SENSITIVE_METADATA_NAMES:
                raise ValueError(
                    f"{path}.{name} may contain secret material and cannot be stored"
                )
            _reject_secret_metadata(nested, f"{path}.{name}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_metadata(nested, f"{path}[{index}]")


class ExternalIdentity(_ActorContract):
    """A credential-free provider subject linked to one Battalion Actor.

    ``integration_id`` scopes a subject to one configured provider instance,
    such as a specific GitHub organization or Discord server.  It is therefore
    the durable disambiguator; provider names alone are insufficient.
    """

    schema_version: Literal["1.0"] = "1.0"
    actor_id: UUID
    integration_id: str = Field(min_length=1, max_length=63)
    provider: str = Field(min_length=1, max_length=63)
    external_subject: str = Field(min_length=1, max_length=500)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("integration_id", "provider")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _INTEGRATION_IDENTIFIER.fullmatch(value):
            raise ValueError(
                "must be a stable lowercase identifier using letters, digits, "
                "and hyphens"
            )
        return value

    @field_validator("metadata")
    @classmethod
    def validate_credential_free_metadata(
        cls, metadata: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        _reject_secret_metadata(metadata)
        return metadata


class ActorBootstrapEvent(_ActorContract):
    """One-time evidence establishing the project's initial local trust root."""

    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    project_id: UUID
    actor_id: UUID
    occurred_at: datetime
    kind: Literal["project-authority-bootstrap"] = "project-authority-bootstrap"
    granted_capabilities: tuple[
        Literal[
            "inspect",
            "operate",
            "resolve-interrupt",
            "approve",
            "assign",
            "administer",
        ],
        ...,
    ] = BOOTSTRAP_CAPABILITIES

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("bootstrap event time must be timezone-aware")
        if self.granted_capabilities != BOOTSTRAP_CAPABILITIES:
            raise ValueError("the bootstrap event must record the accepted initial grants")
        return self


class ActorRegistry(_ActorContract):
    """Versioned Actors and their credential-free external identity bindings."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: UUID
    actors: tuple[Actor, ...] = ()
    external_identities: tuple[ExternalIdentity, ...] = ()
    local_actor_id: UUID | None = None
    bootstrap_event: ActorBootstrapEvent | None = None

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        indexed = {actor.actor_id: actor for actor in self.actors}
        if len(indexed) != len(self.actors):
            raise ValueError("Actor identifiers must be unique")
        external_subjects: set[tuple[str, str]] = set()
        for identity in self.external_identities:
            if identity.actor_id not in indexed:
                raise ValueError("external identity must reference a persisted Actor")
            key = (identity.integration_id, identity.external_subject)
            if key in external_subjects:
                raise ValueError(
                    "integration_id plus external subject must identify only one Actor"
                )
            external_subjects.add(key)
        if self.local_actor_id is not None:
            local = indexed.get(self.local_actor_id)
            if local is None or local.kind is not ActorKind.HUMAN:
                raise ValueError("selected local Actor must reference a human Actor")
        if self.bootstrap_event is None:
            if self.actors or self.external_identities or self.local_actor_id is not None:
                raise ValueError("Actors cannot exist before project bootstrap")
            return self
        event = self.bootstrap_event
        if event.project_id != self.project_id:
            raise ValueError("bootstrap event belongs to a different project")
        bootstrap_actor = indexed.get(event.actor_id)
        if bootstrap_actor is None:
            raise ValueError("bootstrap event must reference a persisted Actor")
        if bootstrap_actor.provenance is not ActorCreationProvenance.FIRST_PROJECT_BOOTSTRAP:
            raise ValueError("bootstrap event must reference the bootstrap Actor")
        return self


def load_actor_registry(project_root: str | Path) -> ActorRegistry:
    """Load validated project-local Actors without creating inferred identity."""
    root = Path(project_root).resolve()
    project = load_project_identity(root)
    path = root / ACTOR_REGISTRY
    if not path.exists():
        return ActorRegistry(project_id=project.project_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        registry = ActorRegistry.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise MalformedActorRegistry(f"Malformed Actor registry at {path}: {exc}") from exc
    if registry.project_id != project.project_id:
        raise MalformedActorRegistry(
            f"Actor registry at {path} belongs to {registry.project_id}, "
            f"not project {project.project_id}."
        )
    return registry


def bootstrap_local_actor(
    project_root: str | Path,
    display_name: str,
    *,
    uuid_factory: Callable[[], UUID] = uuid4,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ActorRegistry:
    """Atomically persist the first human Actor and its credited bootstrap event."""
    root = Path(project_root).resolve()
    project = load_project_identity(root)
    path = root / ACTOR_REGISTRY
    if path.exists():
        registry = load_actor_registry(root)
        if registry.bootstrap_event is not None or registry.actors:
            raise ActorBootstrapConsumed("project Actor bootstrap has already been consumed")
    timestamp = now()
    actor = Actor(
        actor_id=uuid_factory(),
        kind=ActorKind.HUMAN,
        display_name=display_name,
        created_at=timestamp,
        created_by=None,
        provenance=ActorCreationProvenance.FIRST_PROJECT_BOOTSTRAP,
    )
    event = ActorBootstrapEvent(
        event_id=uuid_factory(),
        project_id=project.project_id,
        actor_id=actor.actor_id,
        occurred_at=timestamp,
    )
    registry = ActorRegistry(
        project_id=project.project_id,
        actors=(actor,),
        local_actor_id=actor.actor_id,
        bootstrap_event=event,
    )
    _write_registry(path, registry, create_only=True)
    return registry


def select_local_actor(project_root: str | Path, actor_id: UUID) -> ActorRegistry:
    """Select an existing active human Actor for local, offline operation."""
    root = Path(project_root).resolve()
    registry = load_actor_registry(root)
    actor = next((item for item in registry.actors if item.actor_id == actor_id), None)
    if actor is None:
        raise ActorNotFound(str(actor_id))
    if actor.kind is not ActorKind.HUMAN or actor.status is not ActorStatus.ACTIVE:
        raise ActorError("the selected local Actor must be an active human")
    updated = registry.model_copy(update={"local_actor_id": actor_id})
    _write_registry(root / ACTOR_REGISTRY, updated)
    return updated


def get_actor(project_root: str | Path, actor_id: UUID) -> Actor:
    """Resolve one durable Actor without matching mutable presentation text."""
    registry = load_actor_registry(project_root)
    actor = next((item for item in registry.actors if item.actor_id == actor_id), None)
    if actor is None:
        raise ActorNotFound(str(actor_id))
    return actor


def get_local_actor(project_root: str | Path) -> Actor:
    """Return the explicitly selected active local human Actor."""
    registry = load_actor_registry(project_root)
    if registry.local_actor_id is None:
        raise ActorNotFound("the project has no selected local Actor")
    actor = next(
        (item for item in registry.actors if item.actor_id == registry.local_actor_id),
        None,
    )
    if actor is None:
        raise MalformedActorRegistry("selected local Actor is missing")
    if actor.kind is not ActorKind.HUMAN or actor.status is not ActorStatus.ACTIVE:
        raise ActorUnavailable("the selected local Actor must be an active human")
    return actor


def get_external_identity(
    project_root: str | Path, integration_id: str, external_subject: str
) -> ExternalIdentity:
    """Return one mapping by its unambiguous integration-scoped subject."""
    registry = load_actor_registry(project_root)
    identity = next(
        (
            item
            for item in registry.external_identities
            if item.integration_id == integration_id
            and item.external_subject == external_subject
        ),
        None,
    )
    if identity is None:
        raise ExternalIdentityNotFound(f"{integration_id}:{external_subject}")
    return identity


def resolve_external_actor(
    project_root: str | Path, integration_id: str, external_subject: str
) -> Actor:
    """Resolve identity only; callers still must authorize the returned Actor."""
    identity = get_external_identity(project_root, integration_id, external_subject)
    return get_actor(project_root, identity.actor_id)


def format_actor_attribution(display_name: str, actor_id: UUID | None) -> str:
    """Render literal pre-BTN-59 strings without pretending they are an Actor."""
    if actor_id is None:
        return f"{display_name} (legacy attribution)"
    return display_name


def create_actor(
    project_root: str | Path,
    *,
    kind: ActorKind,
    display_name: str,
    created_by: UUID,
    uuid_factory: Callable[[], UUID] = uuid4,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ActorRegistry:
    """Create a human or system Actor under explicit existing-Actor provenance."""
    root = Path(project_root).resolve()
    registry = load_actor_registry(root)
    creator = next(
        (item for item in registry.actors if item.actor_id == created_by), None
    )
    if creator is None:
        raise ActorNotFound(str(created_by))
    actor = Actor(
        actor_id=uuid_factory(),
        kind=kind,
        display_name=display_name,
        created_at=now(),
        created_by=creator.actor_id,
        provenance=(
            ActorCreationProvenance.SYSTEM_PROVISIONING
            if kind is ActorKind.SYSTEM
            else ActorCreationProvenance.LOCAL_ADMINISTRATION
        ),
    )
    updated = ActorRegistry.model_validate({
        **registry.model_dump(),
        "actors": [*registry.actors, actor],
    })
    _write_registry(root / ACTOR_REGISTRY, updated)
    return updated


def link_external_identity(
    project_root: str | Path,
    *,
    actor_id: UUID,
    integration_id: str,
    provider: str,
    external_subject: str,
    metadata: dict[str, JsonValue] | None = None,
) -> ActorRegistry:
    """Atomically link one provider subject to an existing durable Actor."""
    root = Path(project_root).resolve()
    registry = load_actor_registry(root)
    if not any(actor.actor_id == actor_id for actor in registry.actors):
        raise ActorNotFound(str(actor_id))
    identity = ExternalIdentity(
        actor_id=actor_id,
        integration_id=integration_id,
        provider=provider,
        external_subject=external_subject,
        metadata=metadata or {},
    )
    if any(
        item.integration_id == identity.integration_id
        and item.external_subject == identity.external_subject
        for item in registry.external_identities
    ):
        raise ExternalIdentityAlreadyLinked(
            f"{identity.integration_id}:{identity.external_subject}"
        )
    updated = registry.model_copy(
        update={"external_identities": (*registry.external_identities, identity)}
    )
    _write_registry(root / ACTOR_REGISTRY, updated)
    return updated


def unlink_external_identity(
    project_root: str | Path, integration_id: str, external_subject: str
) -> ActorRegistry:
    """Remove exactly one integration-scoped external identity mapping."""
    root = Path(project_root).resolve()
    registry = load_actor_registry(root)
    retained = tuple(
        item
        for item in registry.external_identities
        if not (
            item.integration_id == integration_id
            and item.external_subject == external_subject
        )
    )
    if len(retained) == len(registry.external_identities):
        raise ExternalIdentityNotFound(f"{integration_id}:{external_subject}")
    updated = registry.model_copy(update={"external_identities": retained})
    _write_registry(root / ACTOR_REGISTRY, updated)
    return updated


def rename_actor(
    project_root: str | Path, actor_id: UUID, display_name: str
) -> ActorRegistry:
    """Change presentation metadata without changing durable identity."""
    root = Path(project_root).resolve()
    registry = load_actor_registry(root)
    found = False
    actors = []
    for actor in registry.actors:
        if actor.actor_id == actor_id:
            actor = Actor.model_validate({
                **actor.model_dump(),
                "display_name": display_name,
            })
            found = True
        actors.append(actor)
    if not found:
        raise ActorNotFound(str(actor_id))
    updated = registry.model_copy(update={"actors": tuple(actors)})
    _write_registry(root / ACTOR_REGISTRY, updated)
    return updated


def _write_registry(
    path: Path, registry: ActorRegistry, *, create_only: bool = False
) -> None:
    # Frozen Pydantic models do not recursively freeze dictionaries.  Revalidate
    # at the sole persistence seam so a caller cannot mutate metadata after
    # construction and then durably introduce a secret or malformed mapping.
    registry = ActorRegistry.model_validate(registry.model_dump(mode="json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(registry.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise ActorBootstrapConsumed(
                    "project Actor bootstrap has already been consumed"
                ) from exc
            os.unlink(temporary)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
