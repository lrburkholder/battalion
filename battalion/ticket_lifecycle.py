"""Pure policy for the post-merge canonical-ticket transition."""

from __future__ import annotations

import re


LEGACY_MARKER = re.compile(r"^Battalion-ticket:\s*#(?P<number>[1-9][0-9]*)\s*$")
MULTI_MARKER = re.compile(
    r"^Battalion-tickets:\s*(?P<numbers>#[1-9][0-9]*(?:\s*,\s*#[1-9][0-9]*)+)\s*$"
)
DECLARATION_PREFIX = re.compile(r"^Battalion-tickets?:")


class TicketLifecycleError(ValueError):
    """The merged pull request is not eligible for an automated transition."""


def linked_issue_numbers(body: str | None) -> tuple[int, ...]:
    """Return the explicitly declared canonical Issue numbers.

    Exactly one full-line declaration is allowed. The singular marker is a
    compatibility path for one Issue; the plural marker requires two or more
    comma-separated Issue numbers.
    """

    declaration_lines = [
        line for line in (body or "").splitlines() if DECLARATION_PREFIX.match(line)
    ]
    if len(declaration_lines) != 1:
        raise TicketLifecycleError(
            "merged PR body must contain exactly one full-line Battalion ticket declaration"
        )

    declaration = declaration_lines[0]
    legacy = LEGACY_MARKER.fullmatch(declaration)
    if legacy is not None:
        return (int(legacy.group("number")),)

    multi = MULTI_MARKER.fullmatch(declaration)
    if multi is None:
        raise TicketLifecycleError(
            "malformed Battalion ticket declaration; expected "
            "'Battalion-ticket: #<issue-number>' or "
            "'Battalion-tickets: #<issue-number>, #<issue-number>[, ...]'"
        )

    numbers = tuple(
        int(token.strip()[1:]) for token in multi.group("numbers").split(",")
    )
    if len(set(numbers)) != len(numbers):
        raise TicketLifecycleError("Battalion ticket declaration contains duplicate Issue numbers")
    return numbers


def linked_issue_number(body: str | None) -> int:
    """Return one explicitly declared Issue number for legacy callers."""

    numbers = linked_issue_numbers(body)
    if len(numbers) != 1:
        raise TicketLifecycleError("ticket declaration contains more than one Issue")
    return numbers[0]


def ensure_in_review(labels: set[str]) -> None:
    """Refuse any lifecycle state other than the reviewed pre-merge state."""

    statuses = sorted(label for label in labels if label.startswith("status:"))
    if statuses != ["status:in-review"]:
        raise TicketLifecycleError(
            "linked Issue must have exactly status:in-review before post-merge closure; "
            f"found {statuses}"
        )
