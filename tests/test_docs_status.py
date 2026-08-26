"""Tests for deterministic GitHub-Issue-backed status projections."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.sync_status import (
    IssueNormalizationError,
    Ticket,
    load_tickets,
    normalize_issues,
    render_delivery_section,
    sync_documents,
)


def issue(
    ticket_id: str = "BTN-12", *, state: str = "OPEN", reason: str = "",
    phase: str = "implementation", priority: str = "P1", role: str = "driver",
    labels: list[str] | None = None, body: str | None = None,
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
            "labels": [{"name": label} for label in labels]}


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
    rendered = render_delivery_section(tickets)
    assert rendered.index("BTN-2") < rendered.index("BTN-12")


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


def test_rendering_is_pure_and_excludes_not_started() -> None:
    rendered = render_delivery_section([
        Ticket("BTN-10", "Shipped", "done", "implementation", "P1", "driver"),
        Ticket("BTN-11", "Planned", "not-started", "design", "unknown", "unknown"),
        Ticket("BTN-12", "Blocked", "blocked", "implementation", "P1", "driver"),
        Ticket("BTN-13", "Cancelled", "cancelled", "testing", "P2", "reviewer"),
    ])
    assert "| BTN-10 | Shipped | Yes |" in rendered
    assert "| BTN-12 | Blocked | Blocked |" in rendered
    assert "| BTN-13 | Cancelled | No |" in rendered
    assert "BTN-11" not in rendered
    assert "canonical [GitHub Issues]" in rendered


def test_sync_uses_injected_offline_reader(tmp_path: Path) -> None:
    status = tmp_path / "docs" / "status.md"
    status.parent.mkdir()
    status.write_text("before\n<!-- BEGIN GENERATED:backlog-delivery -->\nold\n<!-- END GENERATED:backlog-delivery -->\nafter\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("<!-- battalion:status:start -->\nold\n<!-- battalion:status:end -->\n", encoding="utf-8")
    reader = lambda: [issue("BTN-12", state="CLOSED", reason="COMPLETED")]
    assert sync_documents(tmp_path, issue_reader=reader) == ["docs/status.md", "README.md"]
    assert sync_documents(tmp_path, check=True, issue_reader=reader) == []
    assert "| BTN-12 | Example ticket | Yes |" in status.read_text(encoding="utf-8")
    assert status.read_text(encoding="utf-8").strip() in readme.read_text(encoding="utf-8")
