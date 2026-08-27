import pytest

from battalion.ticket_lifecycle import TicketLifecycleError, ensure_in_review, linked_issue_number


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
