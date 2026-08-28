import os
import subprocess
import sys
from pathlib import Path

import pytest

from battalion.ticket_lifecycle import (
    TicketLifecycleError,
    ensure_in_review,
    linked_issue_number,
    linked_issue_numbers,
)
from scripts import complete_merged_ticket
from scripts.complete_merged_ticket import _current_pr_body, _normalization_record
from scripts.sync_status import normalize_issues

ROOT = Path(__file__).resolve().parents[1]


def _issue(
    number: int,
    *,
    state: str = "open",
    status: str | None = "status:in-review",
) -> dict[str, object]:
    labels = [
        {"name": "phase:architecture"},
        {"name": "priority:P1"},
        {"name": "role:architect"},
    ]
    if status is not None:
        labels.append({"name": status})
    return {
        "number": number,
        "title": f"BTN-{number} — Example",
        "body": f"""## Battalion metadata

```yaml
schema_version: 1
ticket_id: BTN-{number}
phase: architecture
priority: P1
assignee_role: architect
```""",
        "state": state,
        "state_reason": "completed" if state == "closed" else None,
        "labels": labels,
    }


def test_explicit_full_line_legacy_marker_selects_one_issue() -> None:
    assert linked_issue_number("Summary\n\nBattalion-ticket: #196\n") == 196
    assert linked_issue_numbers("Summary\n\nBattalion-ticket: #196\n") == (196,)


def test_explicit_multi_ticket_marker_selects_declared_issues_in_order() -> None:
    assert linked_issue_numbers(
        "Summary\n\nBattalion-tickets: #196, #201, #230\n"
    ) == (196, 201, 230)


@pytest.mark.parametrize(
    "body",
    [
        None,
        "Relates to #196",
        "Battalion-ticket: #1\nBattalion-ticket: #2",
        "Battalion-ticket: #1\nBattalion-tickets: #2, #3",
        "Battalion-tickets: #1",
        "Battalion-tickets:",
        "Battalion-tickets: #1 #2",
        "Battalion-tickets: #1, 2",
        "Battalion-tickets: #1, #1",
        "Battalion-tickets: #0, #2",
    ],
)
def test_ticket_declaration_rejects_missing_mixed_duplicate_or_malformed_forms(
    body: str | None,
) -> None:
    with pytest.raises(TicketLifecycleError):
        linked_issue_numbers(body)


def test_legacy_single_issue_helper_rejects_multi_ticket_declaration() -> None:
    with pytest.raises(TicketLifecycleError, match="more than one"):
        linked_issue_number("Battalion-tickets: #1, #2")


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


def test_multi_ticket_lifecycle_validates_all_then_mutates_in_declaration_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    issues = {196: _issue(196), 201: _issue(201)}

    def fake_gh(*args: str) -> object:
        calls.append(args)
        if args == ("repos/lrburkholder/battalion/pulls/99",):
            return {"body": "Battalion-tickets: #196, #201"}
        if len(args) == 1 and args[0].startswith("repos/lrburkholder/battalion/issues/"):
            return issues[int(args[0].rsplit("/", 1)[1])]
        return {}

    monkeypatch.setattr(complete_merged_ticket, "_gh", fake_gh)
    monkeypatch.setenv("GITHUB_REPOSITORY", "lrburkholder/battalion")
    monkeypatch.setenv("PR_NUMBER", "99")

    complete_merged_ticket.main()

    first_mutation = next(index for index, call in enumerate(calls) if call[0] == "-X")
    assert calls[:first_mutation] == [
        ("repos/lrburkholder/battalion/pulls/99",),
        ("repos/lrburkholder/battalion/issues/196",),
        ("repos/lrburkholder/battalion/issues/201",),
    ]
    assert calls[first_mutation:] == [
        (
            "-X",
            "DELETE",
            "repos/lrburkholder/battalion/issues/196/labels/status:in-review",
        ),
        (
            "-X",
            "PATCH",
            "repos/lrburkholder/battalion/issues/196",
            "-f",
            "state=closed",
        ),
        (
            "-X",
            "DELETE",
            "repos/lrburkholder/battalion/issues/201/labels/status:in-review",
        ),
        (
            "-X",
            "PATCH",
            "repos/lrburkholder/battalion/issues/201",
            "-f",
            "state=closed",
        ),
    ]


def test_mixed_eligibility_fails_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    issues = {
        196: _issue(196),
        201: _issue(201, status="status:in-progress"),
    }

    def fake_gh(*args: str) -> object:
        calls.append(args)
        if args == ("repos/lrburkholder/battalion/pulls/99",):
            return {"body": "Battalion-tickets: #196, #201"}
        if len(args) == 1 and args[0].startswith("repos/lrburkholder/battalion/issues/"):
            return issues[int(args[0].rsplit("/", 1)[1])]
        raise AssertionError(f"unexpected mutation: {args}")

    monkeypatch.setattr(complete_merged_ticket, "_gh", fake_gh)
    monkeypatch.setenv("GITHUB_REPOSITORY", "lrburkholder/battalion")
    monkeypatch.setenv("PR_NUMBER", "99")

    with pytest.raises(TicketLifecycleError, match="status:in-review"):
        complete_merged_ticket.main()

    assert not any(call[0] == "-X" for call in calls)


def test_already_completed_issue_is_reconciliation_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    issues = {
        196: _issue(196, state="closed", status=None),
        201: _issue(201),
    }

    def fake_gh(*args: str) -> object:
        calls.append(args)
        if args == ("repos/lrburkholder/battalion/pulls/99",):
            return {"body": "Battalion-tickets: #196, #201"}
        if len(args) == 1 and args[0].startswith("repos/lrburkholder/battalion/issues/"):
            return issues[int(args[0].rsplit("/", 1)[1])]
        return {}

    monkeypatch.setattr(complete_merged_ticket, "_gh", fake_gh)
    monkeypatch.setenv("GITHUB_REPOSITORY", "lrburkholder/battalion")
    monkeypatch.setenv("PR_NUMBER", "99")

    complete_merged_ticket.main()

    mutations = [call for call in calls if call[0] == "-X"]
    assert mutations == [
        (
            "-X",
            "DELETE",
            "repos/lrburkholder/battalion/issues/201/labels/status:in-review",
        ),
        (
            "-X",
            "PATCH",
            "repos/lrburkholder/battalion/issues/201",
            "-f",
            "state=closed",
        ),
    ]


def test_closed_issue_with_active_status_is_not_valid_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> object:
        calls.append(args)
        if args == ("repos/lrburkholder/battalion/pulls/99",):
            return {"body": "Battalion-ticket: #196"}
        if args == ("repos/lrburkholder/battalion/issues/196",):
            return _issue(196, state="closed", status="status:in-review")
        raise AssertionError(f"unexpected mutation: {args}")

    monkeypatch.setattr(complete_merged_ticket, "_gh", fake_gh)
    monkeypatch.setenv("GITHUB_REPOSITORY", "lrburkholder/battalion")
    monkeypatch.setenv("PR_NUMBER", "99")

    with pytest.raises(
        TicketLifecycleError,
        match="schema validation failed.*closed issues cannot retain status labels",
    ):
        complete_merged_ticket.main()

    assert not any(call[0] == "-X" for call in calls)


def test_lifecycle_normalizes_lowercase_rest_closed_state() -> None:
    record = _normalization_record(_issue(199, state="closed", status=None))

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
