"""Close one validated merged-PR ticket.

The GitHub Action supplies trusted repository and pull-request identity through
environment variables. The script fetches the current PR body from GitHub so a
rerun can recover after a maintainer corrects PR metadata. It never executes PR
text and permits only the explicit marker parsed by
:mod:`battalion.ticket_lifecycle`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Direct script execution puts ``scripts/`` rather than the repository root on
# sys.path. The lifecycle Action intentionally does not install Battalion and
# its runtime dependency tree, so make both repository-owned import roots
# explicit before importing either module.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from battalion.ticket_lifecycle import (  # noqa: E402
    TicketLifecycleError,
    ensure_in_review,
    linked_issue_number,
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
        "number": issue.get("number"), "title": issue.get("title"), "body": issue.get("body"),
        "state": state.upper() if isinstance(state, str) else state,
        "stateReason": state_reason.upper() if isinstance(state_reason, str) else state_reason,
        "labels": issue.get("labels"),
    }


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    issue_number = linked_issue_number(_current_pr_body(repository, pr_number))
    issue = _gh(f"repos/{repository}/issues/{issue_number}")
    if not isinstance(issue, dict):
        raise TicketLifecycleError("GitHub returned a non-object Issue payload")
    normalized = _normalization_record(issue)
    try:
        normalize_issues([normalized])
    except IssueNormalizationError as exc:
        raise TicketLifecycleError(f"linked Issue schema validation failed: {exc}") from exc
    if normalized["state"] == "OPEN":
        labels = {label["name"] for label in normalized["labels"] if isinstance(label, dict) and isinstance(label.get("name"), str)}
        ensure_in_review(labels)
        # Removing the active label first avoids ever persisting an invalid
        # closed Issue with a status:* label (ADR-0027).
        _gh("-X", "DELETE", f"repos/{repository}/issues/{issue_number}/labels/status:in-review")
        _gh("-X", "PATCH", f"repos/{repository}/issues/{issue_number}", "-f", "state=closed")
    elif normalized["state"] != "CLOSED":
        raise TicketLifecycleError("linked Issue must be open or already completed")
    print("ticket lifecycle completed; status is rendered at Pages publication time")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TicketLifecycleError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"post-merge ticket lifecycle failed: {exc}") from exc
