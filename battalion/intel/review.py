"""Human-authorized review and promotion of Recon candidate Instincts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from battalion.actors import Actor
from battalion.intel.models import (
    AcceptedInstinct,
    AcceptanceProvenance,
    CandidateInstinct,
    InstinctId,
)
from battalion.intel.repository import IntelRepository


_INSTINCT_ID_ADAPTER = TypeAdapter(InstinctId)


class ReviewAction(str, Enum):
    ACCEPT = "accept"
    EDIT_AND_ACCEPT = "edit-and-accept"
    REJECT = "reject"


class InstinctReviewDecision(BaseModel):
    """Durable audit record for one operator decision about one candidate."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: InstinctId
    action: ReviewAction
    decided_at: datetime
    decided_by: str = Field(min_length=1, max_length=500)
    decided_by_actor_id: UUID | None = None
    accepted_instinct_id: InstinctId | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "InstinctReviewDecision":
        if self.action is ReviewAction.REJECT:
            if self.accepted_instinct_id is not None:
                raise ValueError("a rejected candidate cannot have an accepted identifier")
        elif self.accepted_instinct_id is None:
            raise ValueError("an accepted candidate requires an accepted identifier")
        return self


class DecisionAlreadyRecordedError(FileExistsError):
    """Raised when a candidate already has an immutable operator decision."""


class DecisionNotFoundError(KeyError):
    """Raised when no operator decision exists for a candidate."""


class InstinctDecisionRepository:
    """Append-only local persistence for operator review decisions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def store(self, decision: InstinctReviewDecision) -> None:
        if not isinstance(decision, InstinctReviewDecision):
            raise TypeError("the decision repository stores review decisions only")

        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(decision.candidate_id)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(decision.model_dump_json(indent=2) + "\n")
        except FileExistsError as exc:
            raise DecisionAlreadyRecordedError(
                f"Candidate {decision.candidate_id} already has an immutable decision"
            ) from exc

    def get(self, candidate_id: str) -> InstinctReviewDecision:
        path = self._path_for(candidate_id)
        if not path.is_file():
            raise DecisionNotFoundError(
                f"Candidate {candidate_id} does not have a recorded decision"
            )
        return self._load(path, expected_id=candidate_id)

    def contains(self, candidate_id: str) -> bool:
        return self._path_for(candidate_id).is_file()

    def list_all(self) -> list[InstinctReviewDecision]:
        if not self.root.exists():
            return []
        return [
            self._load(path, expected_id=path.stem)
            for path in sorted(self.root.glob("INS-*.json"), key=lambda item: item.name)
        ]

    def ensure_unrecorded(self, candidate_id: str) -> None:
        if self.contains(candidate_id):
            raise DecisionAlreadyRecordedError(
                f"Candidate {candidate_id} already has an immutable decision"
            )

    def _path_for(self, candidate_id: str) -> Path:
        validated = _INSTINCT_ID_ADAPTER.validate_python(candidate_id)
        return self.root / f"{validated}.json"

    @staticmethod
    def _load(path: Path, *, expected_id: str) -> InstinctReviewDecision:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Decision file at {path} is not valid JSON: {exc}") from exc
        decision = InstinctReviewDecision.model_validate(raw)
        if decision.candidate_id != expected_id:
            raise ValueError(
                f"Candidate identifier {decision.candidate_id} does not match "
                f"decision filename {expected_id}"
            )
        return decision


_EDITABLE_FIELDS = {
    "instinct_id",
    "recommendation",
    "evidence",
    "audience",
    "applicability",
    "tags",
    "supersedes_id",
}


class InstinctReviewWorkflow:
    """The sole application boundary that turns candidates into knowledge."""

    def __init__(
        self,
        intel_repository: IntelRepository,
        decision_repository: InstinctDecisionRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.intel_repository = intel_repository
        self.decision_repository = decision_repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def accept(
        self,
        candidate: CandidateInstinct,
        *,
        decided_by: Actor | str,
        decided_at: datetime | None = None,
    ) -> InstinctReviewDecision:
        return self._promote(
            candidate,
            action=ReviewAction.ACCEPT,
            decided_by=decided_by,
            decided_at=decided_at,
            edits=None,
        )

    def edit_then_accept(
        self,
        candidate: CandidateInstinct,
        *,
        decided_by: Actor | str,
        edits: Mapping[str, Any],
        decided_at: datetime | None = None,
    ) -> InstinctReviewDecision:
        unknown = set(edits) - _EDITABLE_FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"operator cannot edit protected candidate fields: {names}")
        if not edits:
            raise ValueError("edit then accept requires at least one content edit")
        return self._promote(
            candidate,
            action=ReviewAction.EDIT_AND_ACCEPT,
            decided_by=decided_by,
            decided_at=decided_at,
            edits=edits,
        )

    def reject(
        self,
        candidate: CandidateInstinct,
        *,
        decided_by: Actor | str,
        decided_at: datetime | None = None,
    ) -> InstinctReviewDecision:
        self._require_candidate(candidate)
        self.decision_repository.ensure_unrecorded(candidate.instinct_id)
        actor_name, actor_id = _actor_evidence(decided_by)
        decision = InstinctReviewDecision(
            candidate_id=candidate.instinct_id,
            action=ReviewAction.REJECT,
            decided_at=decided_at or self._clock(),
            decided_by=actor_name,
            decided_by_actor_id=actor_id,
        )
        self.decision_repository.store(decision)
        return decision

    def _promote(
        self,
        candidate: CandidateInstinct,
        *,
        action: ReviewAction,
        decided_by: Actor | str,
        decided_at: datetime | None,
        edits: Mapping[str, Any] | None,
    ) -> InstinctReviewDecision:
        self._require_candidate(candidate)
        self.decision_repository.ensure_unrecorded(candidate.instinct_id)
        timestamp = decided_at or self._clock()
        content = candidate.model_dump(exclude={"lifecycle"})
        if edits:
            content.update(dict(edits))
        actor_name, actor_id = _actor_evidence(decided_by)
        accepted = AcceptedInstinct.model_validate({
            **content,
            "lifecycle": "accepted",
            "acceptance_provenance": AcceptanceProvenance(
                accepted_at=timestamp,
                accepted_by=actor_name,
                accepted_by_actor_id=actor_id,
            ),
        })
        decision = InstinctReviewDecision(
            candidate_id=candidate.instinct_id,
            action=action,
            decided_at=timestamp,
            decided_by=actor_name,
            decided_by_actor_id=actor_id,
            accepted_instinct_id=accepted.instinct_id,
        )
        self.intel_repository.store(accepted)
        self.decision_repository.store(decision)
        return decision

    @staticmethod
    def _require_candidate(candidate: CandidateInstinct) -> None:
        if not isinstance(candidate, CandidateInstinct):
            raise TypeError("the review workflow accepts CandidateInstinct values only")


def _actor_evidence(actor: Actor | str) -> tuple[str, UUID | None]:
    """Keep literal legacy strings while new callers provide durable identity."""
    if isinstance(actor, Actor):
        return actor.display_name, actor.actor_id
    return actor, None
