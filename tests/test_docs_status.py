"""Tests for deterministic GitHub-Issue-backed status projections."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.sync_status import (
    GitHubStatusUnavailable,
    IssueNormalizationError,
    Milestone,
    ProjectStatus,
    Ticket,
    fetch_github_status,
    load_tickets,
    normalize_milestones,
    normalize_issues,
    render_delivery_section,
    sync_documents,
)


def issue(
    ticket_id: str = "BTN-12", *, state: str = "OPEN", reason: str = "",
    phase: str = "implementation", priority: str = "P1", role: str = "driver",
    labels: list[str] | None = None, body: str | None = None,
    milestone: str | None = None,
) -> dict[str, object]:
    if body is None:
        body = f"""## Battalion metadata

```yaml
schema_version: 1
ticket_id: {ticket_id}
phase: {phase}
priority: {priority}
assignee_role: {role}
```"""
    if labels is None:
        labels = [f"phase:{phase}"]
        if priority != "unknown":
            labels.append(f"priority:{priority}")
        if role != "unknown":
            labels.append(f"role:{role}")
    return {"number": 12, "title": f"{ticket_id} — Example ticket", "body": body,
            "state": state, "stateReason": reason,
            "labels": [{"name": label} for label in labels],
            "milestone": {"title": milestone} if milestone else None,
            "url": "https://example.test/issues/12"}


@pytest.mark.parametrize(
    ("state", "reason", "status_label", "expected"),
    [
        ("OPEN", "", None, "not-started"),
        ("OPEN", "", "status:in-progress", "in-progress"),
        ("OPEN", "", "status:blocked", "blocked"),
        ("OPEN", "", "status:in-review", "in-review"),
        ("CLOSED", "COMPLETED", None, "done"),
        ("CLOSED", "NOT_PLANNED", None, "cancelled"),
    ],
)
def test_normalization_maps_issue_lifecycle(
    state: str, reason: str, status_label: str | None, expected: str
) -> None:
    raw = issue(state=state, reason=reason)
    if status_label:
        raw["labels"] = [*raw["labels"], {"name": status_label}]  # type: ignore[index]
    assert normalize_issues([raw])[0].status == expected


def test_normalization_orders_and_omits_non_battalion_issues() -> None:
    unrelated = {"number": 99, "title": "Unrelated", "body": "No metadata", "labels": []}
    tickets = load_tickets([
        issue("BTN-12", state="CLOSED", reason="COMPLETED"),
        unrelated,
        issue("BTN-2", state="CLOSED", reason="COMPLETED"),
    ])
    assert [ticket.id for ticket in tickets] == ["BTN-2", "BTN-12"]


def test_battalion_ticket_without_metadata_is_rejected() -> None:
    raw = {"number": 12, "title": "BTN-12 — Missing metadata", "body": "", "labels": []}
    with pytest.raises(IssueNormalizationError, match="requires Issue Schema v1"):
        normalize_issues([raw])


@pytest.mark.parametrize(
    "raw",
    [
        issue(body="## Battalion metadata\n\n```yaml\nticket_id: BTN-12\n```"),
        issue(labels=["phase:implementation", "priority:P0", "role:driver"]),
        issue(labels=["phase:implementation", "priority:P1", "role:driver", "status:unknown"]),
        issue(state="CLOSED", reason="COMPLETED", labels=["phase:implementation", "priority:P1", "role:driver", "status:in-review"]),
    ],
)
def test_normalization_rejects_metadata_and_lifecycle_drift(raw: dict[str, object]) -> None:
    with pytest.raises(IssueNormalizationError):
        normalize_issues([raw])


def test_unknown_classification_uses_no_label_and_design_is_valid_phase() -> None:
    ticket = normalize_issues([issue(phase="design", priority="unknown", role="unknown", labels=["phase:design"])])[0]
    assert (ticket.phase, ticket.priority, ticket.assignee_role) == ("design", "unknown", "unknown")


def test_role_specifier_is_rejected() -> None:
    with pytest.raises(IssueNormalizationError, match="assignee_role"):
        normalize_issues([issue(role="specifier", labels=["phase:implementation", "priority:P1", "role:specifier"])])


def test_rendering_is_pure_and_summarizes_milestones() -> None:
    rendered = render_delivery_section(ProjectStatus((
        Ticket("BTN-10", "Shipped", "done", "implementation", "P1", "driver", 10, "v1"),
        Ticket("BTN-11", "Planned", "not-started", "design", "unknown", "unknown", 11, "v1"),
        Ticket("BTN-12", "Blocked", "blocked", "implementation", "P1", "driver", 12, "v2"),
        Ticket("BTN-13", "Cancelled", "cancelled", "testing", "P2", "reviewer", 13, "v2"),
    ), (
        Milestone(2, "v2", "open"),
        Milestone(1, "v1", "closed"),
        Milestone(3, "BTN-M9 - Empty", "open"),
    )))
    assert "### Milestone overview" in rendered
    assert "| v1 | Closed | 2 | 1 | 1 | 0 | 50% |" in rendered
    assert "| v2 | Open | 2 | 0 | 1 | 1 | 0% |" in rendered
    assert "| BTN-M9 - Empty | Open | 0 | 0 | 0 | 0 | 0% |" in rendered
    assert rendered.index("BTN-M9 - Empty") < rendered.index("v1") < rendered.index("v2")
    assert "| BTN-12 | Blocked | v2 | Blocked |" in rendered
    assert "BTN-10 | Shipped" not in rendered
    assert "Issue-level scope, dependencies" in rendered


def test_milestone_normalization_orders_by_battalion_number_not_github_number() -> None:
    milestones = normalize_milestones([
        {"number": 1, "title": "BTN-M10 - Later", "state": "open"},
        {"number": 99, "title": "BTN-M2 - Earlier", "state": "closed"},
    ])
    assert [milestone.title for milestone in milestones] == [
        "BTN-M2 - Earlier", "BTN-M10 - Later"
    ]


def test_rendering_keeps_unassigned_issues_visible() -> None:
    rendered = render_delivery_section(ProjectStatus(
        (Ticket("BTN-12", "Unassigned", "not-started", "implementation", "P1", "driver"),),
        (),
    ))
    assert "| Unassigned | — | 1 | 0 | 1 | 0 | 0% |" in rendered


def test_fetching_status_requires_both_live_collections() -> None:
    def unavailable(*args, **kwargs):
        raise OSError("network unavailable")

    with pytest.raises(GitHubStatusUnavailable, match="no local status fallback"):
        fetch_github_status(runner=unavailable)


def test_fetching_status_uses_canonical_issue_and_milestone_endpoints() -> None:
    payloads = iter([
        [[_rest_issue(issue("BTN-12", milestone="BTN-M2 - Delivery"))]],
        [[{"number": 2, "title": "BTN-M2 - Delivery", "state": "open"}]],
    ])

    class Completed:
        def __init__(self, payload: object) -> None:
            import json
            self.stdout = json.dumps(payload)

    def runner(command, **kwargs):
        return Completed(next(payloads))

    status = fetch_github_status(runner=runner)
    assert [ticket.id for ticket in status.tickets] == ["BTN-12"]
    assert status.milestones == (Milestone(2, "BTN-M2 - Delivery", "open"),)
    assert status.tickets[0].url == "https://example.test/issues/12"


def test_sync_uses_injected_offline_reader(tmp_path: Path) -> None:
    status = tmp_path / "docs" / "status.md"
    status.parent.mkdir()
    status.write_text("before\n<!-- BEGIN GENERATED:backlog-delivery -->\nold\n<!-- END GENERATED:backlog-delivery -->\nafter\n", encoding="utf-8")
    reader = lambda: [issue("BTN-12", state="CLOSED", reason="COMPLETED")]
    assert sync_documents(tmp_path, issue_reader=reader) == ["docs/status.md"]
    assert sync_documents(tmp_path, check=True, issue_reader=reader) == []
    assert "| Unassigned | — | 1 | 1 | 0 | 0 | 100% |" in status.read_text(encoding="utf-8")


def test_readme_links_to_status_without_owning_generated_payload() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "public status dashboard" in readme
    assert "battalion:status" not in readme
    assert "BEGIN GENERATED:backlog-delivery" not in readme


def test_normalization_rejects_malformed_milestone() -> None:
    with pytest.raises(IssueNormalizationError, match="malformed milestone"):
        normalize_issues([issue(milestone="", ) | {"milestone": {"title": ""}}])


def _rest_issue(raw: dict[str, object]) -> dict[str, object]:
    """Make the compact Issue fixture look like the REST endpoint response."""

    return {
        **raw,
        "state": str(raw["state"]).lower(),
        "state_reason": str(raw["stateReason"]).lower() or None,
    }
