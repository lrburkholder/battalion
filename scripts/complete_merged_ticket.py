"""Close validated merged-PR tickets.

The GitHub Action supplies trusted repository and pull-request identity through
environment variables. The script fetches the current PR body from GitHub so a
rerun can recover after a maintainer corrects PR metadata. It never executes PR
text and permits only the explicit marker parsed by
:mod:`battalion.ticket_lifecycle`.

All declared Issues are fetched and validated before any mutation occurs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from battalion.ticket_lifecycle import (  # noqa: E402
    TicketLifecycleError,
    ensure_in_review,
    linked_issue_numbers,
)
from sync_status import IssueNormalizationError, normalize_issues  # noqa: E402


def _gh(*args: str) -> object:
    completed = subprocess.run(
        ["gh", "api", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return json.loads(completed.stdout)


def _current_pr_body(repository: str, pr_number: str) -> str | None:
    """Fetch current PR metadata rather than the immutable workflow event body."""

    pull_request = _gh(f"repos/{repository}/pulls/{pr_number}")
    if not isinstance(pull_request, dict):
        raise TicketLifecycleError("GitHub returned a non-object pull request payload")
    body = pull_request.get("body")
    if body is not None and not isinstance(body, str):
        raise TicketLifecycleError("GitHub returned an invalid pull request body")
    return body


def _normalization_record(issue: dict[str, object]) -> dict[str, object]:
    """Adapt lowercase REST Issue fields to the canonical normalizer enums."""

    state = issue.get("state")
    state_reason = issue.get("state_reason")
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "body": issue.get("body"),
        "state": state.upper() if isinstance(state, str) else state,
        "stateReason": state_reason.upper() if isinstance(state_reason, str) else state_reason,
        "labels": issue.get("labels"),
    }


def _labels(normalized: dict[str, object]) -> set[str]:
    labels = normalized.get("labels")
    if not isinstance(labels, list):
        return set()
    return {
        label["name"]
        for label in labels
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }


def _validated_issue(repository: str, issue_number: int) -> dict[str, object]:
    """Fetch and validate one declared Issue without mutating GitHub."""

    issue = _gh(f"repos/{repository}/issues/{issue_number}")
    if not isinstance(issue, dict):
        raise TicketLifecycleError(
            f"GitHub returned a non-object Issue payload for #{issue_number}"
        )

    normalized = _normalization_record(issue)
    try:
        normalize_issues([normalized])
    except IssueNormalizationError as exc:
        raise TicketLifecycleError(
            f"linked Issue #{issue_number} schema validation failed: {exc}"
        ) from exc

    state = normalized["state"]
    labels = _labels(normalized)
    if state == "OPEN":
        ensure_in_review(labels)
    elif state == "CLOSED":
        statuses = sorted(label for label in labels if label.startswith("status:"))
        if statuses:
            raise TicketLifecycleError(
                f"already-completed Issue #{issue_number} must not retain lifecycle "
                f"status labels; found {statuses}"
            )
    else:
        raise TicketLifecycleError(
            f"linked Issue #{issue_number} must be open or already completed"
        )
    return normalized


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    issue_numbers = linked_issue_numbers(_current_pr_body(repository, pr_number))

    # Phase 1: fetch and validate the entire declaration before any mutation.
    validated = [
        (issue_number, _validated_issue(repository, issue_number))
        for issue_number in issue_numbers
    ]

    # Phase 2: mutate in declaration order. Closed Issues are reconciliation
    # states and require no further writes.
    for issue_number, normalized in validated:
        if normalized["state"] != "OPEN":
            continue
        _gh(
            "-X",
            "DELETE",
            f"repos/{repository}/issues/{issue_number}/labels/status:in-review",
        )
        _gh(
            "-X",
            "PATCH",
            f"repos/{repository}/issues/{issue_number}",
            "-f",
            "state=closed",
        )

    print(
        f"ticket lifecycle completed for {len(issue_numbers)} declared Issue(s); "
        "status is rendered at Pages publication time"
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TicketLifecycleError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"post-merge ticket lifecycle failed: {exc}") from exc
