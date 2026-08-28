import os
import subprocess
import sys
from pathlib import Path

import pytest

from battalion.ticket_lifecycle import TicketLifecycleError, ensure_in_review, linked_issue_number
from scripts import complete_merged_ticket
from scripts.complete_merged_ticket import _current_pr_body, _normalization_record
from scripts.sync_status import normalize_issues

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_full_line_marker_selects_one_issue() -> None:
    assert linked_issue_number("Summary\n\nBattalion-ticket: #196\n") == 196


@pytest.mark.parametrize("body", [None, "Relates to #196", "Battalion-ticket: #1\nBattalion-ticket: #2"])
def test_marker_must_be_present_exactly_once(body: str | None) -> None:
    with pytest.raises(TicketLifecycleError, match="exactly one"):
        linked_issue_number(body)


def test_only_in_review_is_eligible_for_automatic_closure() -> None:
    ensure_in_review({"phase:implementation", "status:in-review"})
    with pytest.raises(TicketLifecycleError, match="status:in-review"):
        ensure_in_review({"status:in-progress"})


def test_current_pr_body_uses_live_github_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> object:
        calls.append(args)
        return {"body": "Summary\n\nBattalion-ticket: #228\n"}

    monkeypatch.setattr(complete_merged_ticket, "_gh", fake_gh)

    assert _current_pr_body("lrburkholder/battalion", "229") == "Summary\n\nBattalion-ticket: #228\n"
    assert calls == [("repos/lrburkholder/battalion/pulls/229",)]


def test_current_pr_body_rejects_invalid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(complete_merged_ticket, "_gh", lambda *args: [])

    with pytest.raises(TicketLifecycleError, match="non-object pull request"):
        _current_pr_body("lrburkholder/battalion", "229")


def test_lifecycle_normalizes_lowercase_rest_closed_state() -> None:
    record = _normalization_record({
        "number": 199,
        "title": "BTN-128 — Example",
        "body": """## Battalion metadata

```yaml
schema_version: 1
ticket_id: BTN-128
phase: architecture
priority: P1
assignee_role: architect
```""",
        "state": "closed",
        "state_reason": "completed",
        "labels": [
            {"name": "phase:architecture"},
            {"name": "priority:P1"},
            {"name": "role:architect"},
        ],
    })

    assert normalize_issues([record])[0].status == "done"


def test_lifecycle_entry_point_imports_from_clean_checkout() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("GITHUB_REPOSITORY", None)
    env.pop("PR_NUMBER", None)
    completed = subprocess.run(
        [sys.executable, "scripts/complete_merged_ticket.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "post-merge ticket lifecycle failed: 'GITHUB_REPOSITORY'" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
