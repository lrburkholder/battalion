"""Immutable Markdown persistence and review discovery for Recon candidates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import TypeAdapter

from battalion.intel.models import CandidateInstinct, InstinctId
from battalion.intel.review import (
    InstinctDecisionRepository,
    InstinctReviewDecision,
    ReviewAction,
)


_INSTINCT_ID_ADAPTER = TypeAdapter(InstinctId)
_FRONT_MATTER_BOUNDARY = "---\n"
DEFAULT_CANDIDATE_DIR = Path(".battalion/recon/candidates")


class ImmutableCandidateError(FileExistsError):
    """Raised when a caller attempts to replace candidate evidence."""


class CandidateNotFoundError(KeyError):
    """Raised when a candidate identifier is absent from the repository."""


class CandidateDisposition(str, Enum):
    PENDING = "pending"
    PROMOTED = "promoted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CandidateInboxEntry:
    """One immutable candidate projected with its separate review decision."""

    candidate: CandidateInstinct
    disposition: CandidateDisposition
    decision: InstinctReviewDecision | None


class CandidateRepository:
    """A directory-backed, create-only collection of Recon Markdown evidence."""

    def __init__(self, root: str | Path = DEFAULT_CANDIDATE_DIR) -> None:
        self.root = Path(root)

    def store(self, candidate: CandidateInstinct) -> None:
        """Atomically publish a new candidate without replacing an identifier."""

        if not isinstance(candidate, CandidateInstinct):
            raise TypeError("the candidate repository stores CandidateInstinct values only")

        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(candidate.instinct_id)
        temporary = self.root / f".{candidate.instinct_id}.{uuid4().hex}.tmp"
        try:
            self._write_temporary(temporary, _render_document(candidate))
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise ImmutableCandidateError(
                    f"Candidate {candidate.instinct_id} already exists and is immutable"
                ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, candidate_id: str) -> CandidateInstinct:
        path = self._path_for(candidate_id)
        if not path.is_file():
            raise CandidateNotFoundError(f"Candidate {candidate_id} was not found")
        return self._load(path, expected_id=candidate_id)

    def list_all(self) -> list[CandidateInstinct]:
        """Return every candidate in stable identifier order."""

        if not self.root.exists():
            return []
        return [
            self._load(path, expected_id=path.stem)
            for path in sorted(self.root.glob("INS-*.md"), key=lambda item: item.name)
        ]

    def _path_for(self, candidate_id: str) -> Path:
        validated = _INSTINCT_ID_ADAPTER.validate_python(candidate_id)
        return self.root / f"{validated}.md"

    @staticmethod
    def _write_temporary(path: Path, payload: str) -> None:
        with path.open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())

    @staticmethod
    def _load(path: Path, *, expected_id: str) -> CandidateInstinct:
        document = path.read_text(encoding="utf-8")
        if not document.startswith(_FRONT_MATTER_BOUNDARY):
            raise ValueError(f"Candidate file at {path} has no YAML front matter")
        try:
            metadata_text, _ = document[len(_FRONT_MATTER_BOUNDARY):].split(
                "\n---\n", 1
            )
        except ValueError as exc:
            raise ValueError(
                f"Candidate file at {path} has unterminated YAML front matter"
            ) from exc
        metadata = yaml.safe_load(metadata_text)
        candidate = CandidateInstinct.model_validate(metadata)
        if candidate.instinct_id != expected_id:
            raise ValueError(
                f"Candidate identifier {candidate.instinct_id} does not match "
                f"repository filename {expected_id}"
            )
        if document != _render_document(candidate):
            raise ValueError(
                f"Candidate Markdown at {path} does not match its validated metadata"
            )
        return candidate


class CandidateInbox:
    """Deterministic read model over candidate evidence and review decisions."""

    def __init__(
        self,
        candidate_repository: CandidateRepository,
        decision_repository: InstinctDecisionRepository,
    ) -> None:
        self.candidate_repository = candidate_repository
        self.decision_repository = decision_repository

    def list_all(self) -> list[CandidateInboxEntry]:
        entries = []
        for candidate in self.candidate_repository.list_all():
            decision = (
                self.decision_repository.get(candidate.instinct_id)
                if self.decision_repository.contains(candidate.instinct_id)
                else None
            )
            entries.append(CandidateInboxEntry(
                candidate=candidate,
                disposition=_disposition(decision),
                decision=decision,
            ))
        return entries


def _disposition(
    decision: InstinctReviewDecision | None,
) -> CandidateDisposition:
    if decision is None:
        return CandidateDisposition.PENDING
    if decision.action is ReviewAction.REJECT:
        return CandidateDisposition.REJECTED
    return CandidateDisposition.PROMOTED


def _render_document(candidate: CandidateInstinct) -> str:
    metadata = yaml.safe_dump(
        candidate.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
    )
    evidence = "\n".join(
        f"- `{item.run_id}` / `{item.node_execution_id}` — {item.description} "
        f"(`{item.reference}`)"
        for item in candidate.evidence
    )
    audience = ", ".join(f"`{role.value}`" for role in candidate.audience)
    tags = ", ".join(f"`{tag}`" for tag in candidate.tags)
    include = "\n".join(f"- {item}" for item in candidate.applicability.include) or "- None"
    exclude = "\n".join(f"- {item}" for item in candidate.applicability.exclude) or "- None"
    return (
        f"{_FRONT_MATTER_BOUNDARY}{metadata}---\n"
        f"# Recon candidate `{candidate.instinct_id}`\n\n"
        f"## Recommendation\n\n{candidate.recommendation}\n\n"
        f"## Evidence\n\n{evidence}\n\n"
        f"## Audience\n\n{audience}\n\n"
        f"## Applicability\n\n{candidate.applicability.description}\n\n"
        f"### Include\n\n{include}\n\n"
        f"### Exclude\n\n{exclude}\n\n"
        f"## Tags\n\n{tags}\n"
    )
