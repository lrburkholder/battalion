"""Pure policy for the post-merge canonical-ticket transition."""

from __future__ import annotations

import re


MARKER = re.compile(r"(?m)^Battalion-ticket:\s*#(?P<number>[1-9][0-9]*)\s*$")


class TicketLifecycleError(ValueError):
    """The merged pull request is not eligible for an automated transition."""


def linked_issue_number(body: str | None) -> int:
    """Return the one explicitly declared canonical Issue number."""

    matches = MARKER.findall(body or "")
    if len(matches) != 1:
        raise TicketLifecycleError(
            "merged PR body must contain exactly one full-line "
            "'Battalion-ticket: #<issue-number>' marker"
        )
    return int(matches[0])


def ensure_in_review(labels: set[str]) -> None:
    """Refuse any lifecycle state other than the reviewed pre-merge state."""

    statuses = sorted(label for label in labels if label.startswith("status:"))
    if statuses != ["status:in-review"]:
        raise TicketLifecycleError(
            "linked Issue must have exactly status:in-review before post-merge closure; "
            f"found {statuses}"
        )
