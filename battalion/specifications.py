"""Canonical, project-local Specification persistence (BTN-227).

The JSON records in ``.battalion/specifications`` are authoritative.  The
adjacent Markdown file is deliberately a disposable projection: it is never
read to reconstruct, amend, or approve a Specification.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from battalion.actors import Actor
from battalion.identity import load_project_identity


SPECIFICATIONS_DIRECTORY = Path(".battalion/specifications")


class SpecificationError(ValueError):
    """Base error for canonical Specification operations."""


class SpecificationNotFoundError(KeyError, SpecificationError):
    """The requested stable Specification identity is not stored locally."""


class SpecificationRevisionNotFoundError(KeyError, SpecificationError):
    """The requested immutable revision identity is not stored locally."""


class ImmutableSpecificationRevisionError(FileExistsError, SpecificationError):
    """A caller attempted to replace a historical revision."""


class InvalidSpecificationTransition(SpecificationError):
    """A lifecycle operation did not target an eligible revision."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SpecificationRevisionStatus(str, Enum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SpecificationReference(_Contract):
    """A bounded pointer to governing or supporting evidence."""

    kind: str = Field(min_length=1, max_length=100)
    reference: str = Field(min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=2000)


class SpecificationProducerProvenance(_Contract):
    """Producer evidence; a model identity alone never grants authority."""

    producer: str = Field(min_length=1, max_length=200)
    execution_id: str | None = Field(default=None, min_length=1, max_length=200)
    model_identity: str | None = Field(default=None, min_length=1, max_length=500)
    prompt_identity: str | None = Field(default=None, min_length=1, max_length=500)
    configuration_identity: str | None = Field(default=None, min_length=1, max_length=500)


class SpecificationAcceptanceProvenance(_Contract):
    accepted_by: str = Field(min_length=1, max_length=200)
    accepted_by_actor_id: UUID | None = None
    accepted_at: datetime

    @field_validator("accepted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("acceptance timestamp must include a timezone")
        return value


class SpecificationRevision(_Contract):
    """One immutable specification snapshot or immutable lifecycle outcome.

    Acceptance, rejection, and supersession create new records which point to
    the exact candidate or accepted source.  This keeps the original candidate
    byte-for-byte and semantically immutable while still giving every outcome a
    stable, revision-pinnable identity.
    """

    schema_version: Literal["1.0"] = "1.0"
    revision_id: UUID
    specification_id: UUID
    status: SpecificationRevisionStatus
    outcomes: tuple[str, ...] = Field(default=(), max_length=100)
    constraints: tuple[str, ...] = Field(default=(), max_length=100)
    non_goals: tuple[str, ...] = Field(default=(), max_length=100)
    acceptance_criteria: tuple[str, ...] = Field(default=(), max_length=100)
    unresolved_product_decisions: tuple[str, ...] = Field(default=(), max_length=100)
    governing_references: tuple[SpecificationReference, ...] = Field(default=(), max_length=100)
    evidence_references: tuple[SpecificationReference, ...] = Field(default=(), max_length=100)
    producer_provenance: SpecificationProducerProvenance | None = None
    created_at: datetime
    source_revision_id: UUID | None = None
    acceptance_provenance: SpecificationAcceptanceProvenance | None = None

    @field_validator("created_at")
    @classmethod
    def require_created_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("revision timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_lifecycle_evidence(self) -> "SpecificationRevision":
        if self.source_revision_id == self.revision_id:
            raise ValueError("a revision cannot reference itself")
        if self.status is SpecificationRevisionStatus.CANDIDATE:
            if self.source_revision_id is not None or self.acceptance_provenance is not None:
                raise ValueError("candidate revisions cannot carry a lifecycle source or acceptance")
        elif self.source_revision_id is None:
            raise ValueError("lifecycle outcome revisions require their exact source revision")
        if self.status is SpecificationRevisionStatus.ACCEPTED:
            if self.acceptance_provenance is None:
                raise ValueError("accepted revisions require human acceptance provenance")
        elif self.acceptance_provenance is not None:
            raise ValueError("only accepted revisions carry acceptance provenance")
        return self


class Specification(_Contract):
    """Stable logical Specification identity with its current authority pointer."""

    schema_version: Literal["1.0"] = "1.0"
    specification_id: UUID
    project_id: UUID
    canonical_work_identity: str = Field(min_length=1, max_length=500)
    current_accepted_revision_id: UUID | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_created_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("specification timestamp must include a timezone")
        return value


class ProjectionState(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    FAILED = "failed"


class SpecificationProjectionStatus(_Contract):
    """Non-authoritative health evidence for the generated Markdown view."""

    schema_version: Literal["1.0"] = "1.0"
    specification_id: UUID
    source_revision_id: UUID | None = None
    state: ProjectionState
    rendered_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checked_at: datetime
    detail: str | None = Field(default=None, max_length=2000)


class SpecificationRepository:
    """Project-local authoritative store with append-only revision files."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def create(
        self,
        canonical_work_identity: str,
        *,
        specification_id: UUID | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> Specification:
        project = load_project_identity(self.project_root, create=True)
        specification = Specification(
            specification_id=specification_id or uuid4(),
            project_id=project.project_id,
            canonical_work_identity=canonical_work_identity,
            created_at=now(),
        )
        directory = self._directory(specification.specification_id)
        directory.mkdir(parents=True, exist_ok=False)
        try:
            _write_exclusive(directory / "specification.json", specification)
        except BaseException:
            directory.rmdir()
            raise
        return specification

    def get(self, specification_id: UUID | str) -> Specification:
        path = self._directory(specification_id) / "specification.json"
        if not path.is_file():
            raise SpecificationNotFoundError(str(specification_id))
        specification = _read_model(path, Specification, "Specification")
        self._validate_project(specification)
        return specification

    def create_candidate(
        self,
        specification_id: UUID | str,
        *,
        outcomes: Iterable[str] = (),
        constraints: Iterable[str] = (),
        non_goals: Iterable[str] = (),
        acceptance_criteria: Iterable[str] = (),
        unresolved_product_decisions: Iterable[str] = (),
        governing_references: Iterable[SpecificationReference] = (),
        evidence_references: Iterable[SpecificationReference] = (),
        producer_provenance: SpecificationProducerProvenance | None = None,
        revision_id: UUID | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> SpecificationRevision:
        specification = self.get(specification_id)
        candidate = SpecificationRevision(
            revision_id=revision_id or uuid4(), specification_id=specification.specification_id,
            status=SpecificationRevisionStatus.CANDIDATE, outcomes=tuple(outcomes),
            constraints=tuple(constraints), non_goals=tuple(non_goals),
            acceptance_criteria=tuple(acceptance_criteria),
            unresolved_product_decisions=tuple(unresolved_product_decisions),
            governing_references=tuple(governing_references), evidence_references=tuple(evidence_references),
            producer_provenance=producer_provenance, created_at=now(),
        )
        self._store_revision(candidate)
        self._mark_projection_stale(specification, candidate.revision_id, candidate.created_at)
        return candidate

    def get_revision(self, specification_id: UUID | str, revision_id: UUID | str) -> SpecificationRevision:
        specification = self.get(specification_id)
        path = self._revision_path(specification.specification_id, revision_id)
        if not path.is_file():
            raise SpecificationRevisionNotFoundError(str(revision_id))
        revision = _read_model(path, SpecificationRevision, "Specification revision")
        if revision.revision_id != UUID(str(revision_id)) or revision.specification_id != specification.specification_id:
            raise SpecificationError("revision identity does not match its repository location")
        return revision

    def list_revisions(self, specification_id: UUID | str) -> list[SpecificationRevision]:
        specification = self.get(specification_id)
        directory = self._directory(specification.specification_id) / "revisions"
        if not directory.exists():
            return []
        return [
            self.get_revision(specification.specification_id, path.stem)
            for path in sorted(directory.glob("*.json"), key=lambda item: item.name)
        ]

    def accept(
        self, specification_id: UUID | str, candidate_revision_id: UUID | str, *,
        accepted_by: Actor | str, accepted_at: datetime | None = None,
        revision_id: UUID | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> SpecificationRevision:
        specification = self.get(specification_id)
        candidate = self.get_revision(specification.specification_id, candidate_revision_id)
        if candidate.status is not SpecificationRevisionStatus.CANDIDATE:
            raise InvalidSpecificationTransition("only an exact candidate revision may be accepted")
        timestamp = accepted_at or clock()
        actor_name, actor_id = _actor_evidence(accepted_by)
        accepted = self._outcome_revision(
            candidate, SpecificationRevisionStatus.ACCEPTED, revision_id=revision_id or uuid4(),
            timestamp=timestamp,
            acceptance=SpecificationAcceptanceProvenance(
                accepted_by=actor_name, accepted_by_actor_id=actor_id, accepted_at=timestamp,
            ),
        )
        self._store_revision(accepted)
        self._save_specification(specification.model_copy(
            update={"current_accepted_revision_id": accepted.revision_id}
        ))
        self._mark_projection_stale(specification, accepted.revision_id, timestamp)
        return accepted

    def reject(self, specification_id: UUID | str, candidate_revision_id: UUID | str, *,
               revision_id: UUID | None = None,
               now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> SpecificationRevision:
        candidate = self.get_revision(specification_id, candidate_revision_id)
        if candidate.status is not SpecificationRevisionStatus.CANDIDATE:
            raise InvalidSpecificationTransition("only a candidate revision may be rejected")
        rejected = self._outcome_revision(candidate, SpecificationRevisionStatus.REJECTED,
                                          revision_id=revision_id or uuid4(), timestamp=now())
        self._store_revision(rejected)
        self._mark_projection_stale(self.get(specification_id), rejected.revision_id, rejected.created_at)
        return rejected

    def supersede(self, specification_id: UUID | str, accepted_revision_id: UUID | str, *,
                  revision_id: UUID | None = None,
                  now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> SpecificationRevision:
        accepted = self.get_revision(specification_id, accepted_revision_id)
        if accepted.status is not SpecificationRevisionStatus.ACCEPTED:
            raise InvalidSpecificationTransition("only an accepted revision may be superseded")
        superseded = self._outcome_revision(accepted, SpecificationRevisionStatus.SUPERSEDED,
                                            revision_id=revision_id or uuid4(), timestamp=now())
        self._store_revision(superseded)
        specification = self.get(specification_id)
        if specification.current_accepted_revision_id == accepted.revision_id:
            self._save_specification(specification.model_copy(update={"current_accepted_revision_id": None}))
        self._mark_projection_stale(specification, superseded.revision_id, superseded.created_at)
        return superseded

    def render_markdown(self, specification_id: UUID | str) -> str:
        specification = self.get(specification_id)
        revision = (self.get_revision(specification.specification_id, specification.current_accepted_revision_id)
                    if specification.current_accepted_revision_id else None)
        lines = ["# Specification", "", f"- Specification ID: `{specification.specification_id}`",
                 f"- Project ID: `{specification.project_id}`", f"- Work identity: `{specification.canonical_work_identity}`"]
        if revision is None:
            lines.extend(["- Current accepted revision: none", ""])
            return "\n".join(lines)
        lines.extend([f"- Current accepted revision: `{revision.revision_id}`", "",
                      "## Revision metadata", "", f"- Revision ID: `{revision.revision_id}`",
                      f"- Status: `{revision.status.value}`", f"- Source candidate revision: `{revision.source_revision_id}`",
                      f"- Accepted at: `{revision.acceptance_provenance.accepted_at.isoformat()}`",
                      f"- Accepted by: {revision.acceptance_provenance.accepted_by}", ""])
        for title, values in (("Outcomes", revision.outcomes), ("Constraints", revision.constraints),
                              ("Non-goals", revision.non_goals), ("Acceptance criteria", revision.acceptance_criteria),
                              ("Unresolved product decisions", revision.unresolved_product_decisions)):
            lines.extend([f"## {title}", ""])
            lines.extend([f"- {value}" for value in values] or ["- None"])
            lines.append("")
        return "\n".join(lines)

    def write_projection(self, specification_id: UUID | str, *,
                         now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Path:
        specification = self.get(specification_id)
        rendered = self.render_markdown(specification.specification_id)
        path = self._directory(specification.specification_id) / "specification.md"
        try:
            _write_text_atomic(path, rendered)
        except OSError as exc:
            self._save_projection_status(SpecificationProjectionStatus(
                specification_id=specification.specification_id, state=ProjectionState.FAILED,
                checked_at=now(), detail=str(exc),
            ))
            raise
        self._save_projection_status(SpecificationProjectionStatus(
            specification_id=specification.specification_id,
            source_revision_id=specification.current_accepted_revision_id,
            state=ProjectionState.FRESH, rendered_digest=_digest(rendered), checked_at=now(),
        ))
        return path

    def projection_status(self, specification_id: UUID | str, *,
                          now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> SpecificationProjectionStatus:
        specification = self.get(specification_id)
        path = self._directory(specification.specification_id) / "projection.json"
        if not path.exists():
            return SpecificationProjectionStatus(specification_id=specification.specification_id,
                                                 state=ProjectionState.STALE, checked_at=now(), detail="not generated")
        recorded = _read_model(path, SpecificationProjectionStatus, "Specification projection status")
        if recorded.state is ProjectionState.FAILED:
            return recorded
        markdown = self._directory(specification.specification_id) / "specification.md"
        if (recorded.state is not ProjectionState.FRESH or not markdown.is_file()
                or _digest(markdown.read_text(encoding="utf-8")) != recorded.rendered_digest
                or recorded.source_revision_id != specification.current_accepted_revision_id):
            return SpecificationProjectionStatus(specification_id=specification.specification_id,
                source_revision_id=specification.current_accepted_revision_id, state=ProjectionState.STALE,
                checked_at=now(), detail="projection bytes or canonical source revision differ")
        return recorded

    def _outcome_revision(self, source: SpecificationRevision, status: SpecificationRevisionStatus, *,
                          revision_id: UUID, timestamp: datetime,
                          acceptance: SpecificationAcceptanceProvenance | None = None) -> SpecificationRevision:
        return SpecificationRevision(**{**source.model_dump(), "revision_id": revision_id, "status": status,
            "created_at": timestamp, "source_revision_id": source.revision_id,
            "acceptance_provenance": acceptance})

    def _store_revision(self, revision: SpecificationRevision) -> None:
        self.get(revision.specification_id)
        try:
            _write_exclusive(self._revision_path(revision.specification_id, revision.revision_id), revision)
        except FileExistsError as exc:
            raise ImmutableSpecificationRevisionError(
                f"Specification revision {revision.revision_id} already exists and is immutable"
            ) from exc

    def _save_specification(self, specification: Specification) -> None:
        self._validate_project(specification)
        _write_model_atomic(self._directory(specification.specification_id) / "specification.json", specification)

    def _mark_projection_stale(self, specification: Specification, revision_id: UUID, timestamp: datetime) -> None:
        self._save_projection_status(SpecificationProjectionStatus(
            specification_id=specification.specification_id, source_revision_id=revision_id,
            state=ProjectionState.STALE, checked_at=timestamp, detail="canonical state changed",
        ))

    def _save_projection_status(self, status: SpecificationProjectionStatus) -> None:
        _write_model_atomic(self._directory(status.specification_id) / "projection.json", status)

    def _validate_project(self, specification: Specification) -> None:
        project = load_project_identity(self.project_root, create=True)
        if specification.project_id != project.project_id:
            raise SpecificationError("Specification belongs to a different project")

    def _directory(self, specification_id: UUID | str) -> Path:
        return self.project_root / SPECIFICATIONS_DIRECTORY / str(UUID(str(specification_id)))

    def _revision_path(self, specification_id: UUID | str, revision_id: UUID | str) -> Path:
        return self._directory(specification_id) / "revisions" / f"{UUID(str(revision_id))}.json"


def _actor_evidence(actor: Actor | str) -> tuple[str, UUID | None]:
    return (actor.display_name, actor.actor_id) if isinstance(actor, Actor) else (actor, None)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _read_model(path: Path, model: type[BaseModel], label: str):
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise SpecificationError(f"Malformed {label} at {path}: {exc}") from exc


def _write_exclusive(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as destination:
        destination.write(model.model_dump_json(indent=2) + "\n")


def _write_model_atomic(path: Path, model: BaseModel) -> None:
    _write_text_atomic(path, model.model_dump_json(indent=2) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
