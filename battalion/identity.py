"""Durable run and project identity contracts.

Canonical run identifiers are UUIDs. Human-readable aliases remain display
metadata and are never used as persistence keys for new runs. Project identity
lives with project-local Battalion data so it survives a repository move.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError

from battalion.state.models import RunState
from battalion.state.persistence import load_state


PROJECT_MARKER = Path(".battalion/project.json")
RUN_CATALOG = Path(".battalion/runs.json")
STATE_DIRECTORY = Path(".battalion/state")


class IdentityError(ValueError):
    """Base class for identity and catalog failures."""


class MalformedIdentity(IdentityError):
    """A durable identity file exists but cannot be trusted."""


class ProjectCatalogMismatch(IdentityError):
    """Run or catalog evidence belongs to a different project."""


class DuplicateProjectIdentity(IdentityError):
    """Two live paths claim the same project identity."""


class RunIdentity(BaseModel):
    run_id: str
    display_alias: str = Field(min_length=1, max_length=300)


class ProjectIdentity(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: UUID
    created_at: datetime


class RunCatalogEntry(BaseModel):
    run_id: str
    display_alias: str = Field(min_length=1, max_length=300)
    ticket_id: str
    state_path: str
    legacy_id: bool = False


class ProjectRunCatalog(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: UUID
    runs: list[RunCatalogEntry] = Field(default_factory=list)


def generate_run_identity(
    ticket_id: str, *, uuid_factory: Callable[[], UUID] = uuid4
) -> RunIdentity:
    """Generate an opaque canonical ID and a separate meaningful alias."""
    canonical = uuid_factory()
    return RunIdentity(
        run_id=str(canonical),
        display_alias=f"{ticket_id}-{str(canonical)[:8]}",
    )


def is_canonical_run_id(run_id: str) -> bool:
    try:
        return str(UUID(run_id)) == run_id.lower()
    except (ValueError, AttributeError):
        return False


def load_project_identity(
    project_root: str | Path,
    *,
    create: bool = False,
    uuid_factory: Callable[[], UUID] = uuid4,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ProjectIdentity:
    """Load the project-local marker, optionally creating it atomically."""
    root = Path(project_root).resolve()
    marker = root / PROJECT_MARKER
    if not marker.exists():
        if not create:
            raise FileNotFoundError(f"No project identity at {marker}")
        identity = ProjectIdentity(project_id=uuid_factory(), created_at=now())
        _write_model(marker, identity)
        return identity

    return _read_model(marker, ProjectIdentity, "project identity")


def open_projects(project_roots: Iterable[str | Path]) -> list[ProjectIdentity]:
    """Open multiple projects without silently coalescing copied identities."""
    opened: list[ProjectIdentity] = []
    seen: dict[UUID, Path] = {}
    for project_root in project_roots:
        root = Path(project_root).resolve()
        identity = load_project_identity(root)
        prior = seen.get(identity.project_id)
        if prior is not None and prior != root:
            raise DuplicateProjectIdentity(
                f"Project {identity.project_id} is present at both {prior} and {root}; "
                "reconcile the copy explicitly before opening both."
            )
        seen[identity.project_id] = root
        opened.append(identity)
    return opened


def load_run_catalog(
    project_root: str | Path, *, discover_legacy: bool = False
) -> ProjectRunCatalog:
    """Load the project catalog and optionally project legacy state into it.

    Legacy discovery is a read-only compatibility projection. It does not
    rewrite historical state or pretend a human-readable identifier is a UUID.
    """
    root = Path(project_root).resolve()
    identity = load_project_identity(root)
    path = root / RUN_CATALOG
    if path.exists():
        catalog = _read_model(path, ProjectRunCatalog, "run catalog")
        if catalog.project_id != identity.project_id:
            raise ProjectCatalogMismatch(
                f"Run catalog at {path} belongs to {catalog.project_id}, "
                f"not project {identity.project_id}."
            )
    else:
        catalog = ProjectRunCatalog(project_id=identity.project_id)

    if not discover_legacy:
        return catalog

    known = {entry.run_id for entry in catalog.runs}
    known_paths = {
        (
            Path(entry.state_path)
            if Path(entry.state_path).is_absolute()
            else root / entry.state_path
        ).resolve()
        for entry in catalog.runs
    }
    projected = list(catalog.runs)
    state_dir = root / STATE_DIRECTORY
    if state_dir.exists():
        for state_path in sorted(state_dir.glob("*.json")):
            if state_path.resolve() in known_paths:
                continue
            state = load_state(state_path)
            if state.run_id in known:
                continue
            _assert_state_project(state, identity)
            projected.append(_entry_for(state, root, state_path))
            known.add(state.run_id)
    return catalog.model_copy(update={"runs": projected})


def register_run(
    state: RunState,
    project_root: str | Path,
    *,
    state_path: str | Path | None = None,
) -> ProjectRunCatalog:
    """Persist one canonical run reference in its project-local catalog."""
    root = Path(project_root).resolve()
    identity = load_project_identity(root)
    _assert_state_project(state, identity)
    if not is_canonical_run_id(state.run_id):
        raise IdentityError(
            f"New catalog entries require a UUID run ID, got {state.run_id!r}."
        )
    catalog = load_run_catalog(root)
    if any(entry.run_id == state.run_id for entry in catalog.runs):
        return catalog

    durable_path = Path(state_path) if state_path is not None else root / STATE_DIRECTORY / f"{state.run_id}.json"
    entry = _entry_for(state, root, durable_path)
    updated = catalog.model_copy(update={"runs": [*catalog.runs, entry]})
    _write_model(root / RUN_CATALOG, updated)
    return updated


def _assert_state_project(state: RunState, identity: ProjectIdentity) -> None:
    if state.project_id is None:
        return  # pre-BTN-32 state remains readable as legacy evidence
    try:
        state_project_id = UUID(state.project_id)
    except ValueError as exc:
        raise MalformedIdentity(
            f"Run {state.run_id!r} has malformed project ID {state.project_id!r}."
        ) from exc
    if state_project_id != identity.project_id:
        raise ProjectCatalogMismatch(
            f"Run {state.run_id!r} belongs to project {state_project_id}, "
            f"not {identity.project_id}."
        )


def _entry_for(state: RunState, root: Path, state_path: Path) -> RunCatalogEntry:
    try:
        relative_path = state_path.resolve().relative_to(root).as_posix()
    except ValueError:
        relative_path = str(state_path.resolve())
    legacy = not is_canonical_run_id(state.run_id)
    alias = state.run_alias or state.run_id
    return RunCatalogEntry(
        run_id=state.run_id,
        display_alias=alias,
        ticket_id=state.ticket_id,
        state_path=relative_path,
        legacy_id=legacy,
    )


def _read_model(path: Path, model_type, label: str):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return model_type.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise MalformedIdentity(f"Malformed {label} at {path}: {exc}") from exc


def _write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(model.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
