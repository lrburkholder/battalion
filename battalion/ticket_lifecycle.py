"""Pure policy for Battalion canonical-ticket lifecycle transitions."""

from __future__ import annotations

import re


LEGACY_MARKER = re.compile(r"^Battalion-ticket:\s*#(?P<number>[1-9][0-9]*)\s*$")
MULTI_MARKER = re.compile(
    r"^Battalion-tickets:\s*(?P<numbers>#[1-9][0-9]*(?:\s*,\s*#[1-9][0-9]*)+)\s*$"
)
DECLARATION_PREFIX = re.compile(r"^Battalion-tickets?:")
GITHUB_CLOSING_REFERENCE = re.compile(
    r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\b"
    r"\s*:?\s*#(?P<number>[1-9][0-9]*)\b",
    re.IGNORECASE,
)


class TicketLifecycleError(ValueError):
    """The pull request is not eligible for the requested lifecycle transition."""


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


def ensure_no_declared_auto_close(body: str | None) -> tuple[int, ...]:
    """Reject GitHub auto-close keywords targeting Battalion-declared Issues."""

    numbers = linked_issue_numbers(body)
    declared = set(numbers)
    conflicting = sorted(
        {
            int(match.group("number"))
            for match in GITHUB_CLOSING_REFERENCE.finditer(body or "")
            if int(match.group("number")) in declared
        }
    )
    if conflicting:
        refs = ", ".join(f"#{number}" for number in conflicting)
        raise TicketLifecycleError(
            "Battalion owns closure for declared ticket(s) "
            f"{refs}; remove GitHub auto-close keywords such as Closes/Fixes/Resolves "
            "and keep the Battalion-ticket declaration"
        )
    return numbers


def ensure_in_review(labels: set[str]) -> None:
    """Refuse any lifecycle state other than the reviewed pre-merge state."""

    statuses = sorted(label for label in labels if label.startswith("status:"))
    if statuses != ["status:in-review"]:
        raise TicketLifecycleError(
            "linked Issue must have exactly status:in-review before post-merge closure; "
            f"found {statuses}"
        )
