"""Deterministic selection of accepted, active Instincts (BTN-24)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from battalion.intel.models import AcceptedInstinct, InstinctAudience


class ActiveInstinctSource(Protocol):
    """Persistence-independent source of accepted, active Instincts."""

    def list_active(self) -> list[AcceptedInstinct]: ...


_SEPARATOR_RE = re.compile(r"[^\w]+", re.UNICODE)


def _normalized(value: str) -> str:
    """Normalize human-authored matching terms without semantic inference."""

    return " ".join(_SEPARATOR_RE.sub(" ", value.casefold()).split())


def _matches(term: str, task_text: str) -> bool:
    normalized_term = _normalized(term)
    return bool(normalized_term) and normalized_term in task_text


@dataclass(frozen=True)
class RetrievalDecision:
    """Inspectable reason that an active Instinct was included or excluded."""

    instinct_id: str
    included: bool
    reason: str


@dataclass(frozen=True)
class RetrievalResult:
    selected: tuple[AcceptedInstinct, ...]
    decisions: tuple[RetrievalDecision, ...]


class InstinctRetriever:
    """Apply explicit audience, applicability, tag, and ordering rules.

    The repository supplies only accepted, active, non-superseded records.
    Audience and applicability determine eligibility. Applicability exclusions
    win over inclusions. Matching uses normalized literal phrases. Eligible
    records are ordered by descending include matches, descending tag matches,
    then stable Instinct identifier.
    """

    def __init__(self, repository: ActiveInstinctSource) -> None:
        self.repository = repository

    def retrieve(
        self,
        role: InstinctAudience,
        task_text: str,
    ) -> RetrievalResult:
        if not isinstance(role, InstinctAudience):
            role = InstinctAudience(role)
        normalized_task = _normalized(task_text)
        ranked: list[tuple[int, int, str, AcceptedInstinct]] = []
        decisions: list[RetrievalDecision] = []

        for instinct in self.repository.list_active():
            if role not in instinct.audience:
                decisions.append(RetrievalDecision(
                    instinct.instinct_id,
                    False,
                    f"audience does not include {role.value}",
                ))
                continue

            excluded = next(
                (
                    term
                    for term in instinct.applicability.exclude
                    if _matches(term, normalized_task)
                ),
                None,
            )
            if excluded is not None:
                decisions.append(RetrievalDecision(
                    instinct.instinct_id,
                    False,
                    f"excluded by applicability term {excluded!r}",
                ))
                continue

            include_matches = sum(
                _matches(term, normalized_task)
                for term in instinct.applicability.include
            )
            if instinct.applicability.include and include_matches == 0:
                decisions.append(RetrievalDecision(
                    instinct.instinct_id,
                    False,
                    "no applicability include term matched",
                ))
                continue

            tag_matches = sum(_matches(tag, normalized_task) for tag in instinct.tags)
            ranked.append((
                -include_matches,
                -tag_matches,
                instinct.instinct_id,
                instinct,
            ))
            decisions.append(RetrievalDecision(
                instinct.instinct_id,
                True,
                "included: audience matched; "
                f"applicability matches={include_matches}; tag matches={tag_matches}",
            ))

        ranked.sort(key=lambda item: item[:3])
        return RetrievalResult(
            selected=tuple(item[3] for item in ranked),
            decisions=tuple(decisions),
        )
