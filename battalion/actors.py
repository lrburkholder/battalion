"""Project-local durable Actor identity and first-use bootstrap persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    """Versioned project Actor collection and selected local human identity."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: UUID
    actors: tuple[Actor, ...] = ()
    local_actor_id: UUID | None = None
    bootstrap_event: ActorBootstrapEvent | None = None

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        indexed = {actor.actor_id: actor for actor in self.actors}
        if len(indexed) != len(self.actors):
            raise ValueError("Actor identifiers must be unique")
        if self.local_actor_id is not None:
            local = indexed.get(self.local_actor_id)
            if local is None or local.kind is not ActorKind.HUMAN:
                raise ValueError("selected local Actor must reference a human Actor")
        if self.bootstrap_event is None:
            if self.actors or self.local_actor_id is not None:
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
