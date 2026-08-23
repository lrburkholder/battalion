"""Reconciliation tests for generated status documentation (ADR-0027).

``docs/status.md`` is the single human-readable status view; its delivered-
work region is generated from ``backlog.json`` and the document is embedded
into ``README.md`` by ``scripts/sync_status.py``. These tests fail when any
copy drifts from the canonical backlog or when authored claims cite tickets
that do not support them.
"""
from __future__ import annotations

import re

from scripts.sync_status import (
    GENERATED_END,
    GENERATED_START,
    REPOSITORY_ROOT,
    load_tickets,
    render_delivery_section,
    sync_documents,
)

BACKLOG = REPOSITORY_ROOT / "backlog.json"
STATUS_DOC = REPOSITORY_ROOT / "docs" / "status.md"
README = REPOSITORY_ROOT / "README.md"

_TICKET_REF = re.compile(r"BTN-(\d+)")
_EN_DASH_RANGE = re.compile(r"BTN-(\d+)[\u2013\u2014-](\d+)")


def _status_text() -> str:
    return STATUS_DOC.read_text(encoding="utf-8")


def _generated_region() -> str:
    text = _status_text()
    pattern = re.compile(
        re.escape(GENERATED_START) + r".*?" + re.escape(GENERATED_END),
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match is not None, "generated markers missing from docs/status.md"
    return match.group(0)


def test_embedded_and_generated_copies_are_current():
    """sync --check is the drift gate for README and the status document."""
    assert sync_documents(REPOSITORY_ROOT, check=True) == []


def test_generated_region_matches_canonical_backlog():
    tickets = load_tickets(BACKLOG)
    assert _generated_region() == render_delivery_section(tickets)


def test_authored_ticket_references_resolve():
    """Every BTN citation outside the generated region must exist, and
    en-dash ranges (BTN-52–55) expand to real ticket IDs."""
    tickets = {t.id for t in load_tickets(BACKLOG)}
    authored = _status_text().replace(_generated_region(), "")

    referenced: set[str] = set()
    position = 0
    for match in _EN_DASH_RANGE.finditer(authored):
        segment = authored[position:match.start()]
        referenced |= {f"BTN-{n}" for n in _TICKET_REF.findall(segment)}
        start, end = int(match.group(1)), int(match.group(2))
        assert start < end <= start + 40, f"implausible range: {match.group(0)}"
        referenced.update(f"BTN-{number}" for number in range(start, end + 1))
        position = match.end()
    tail = authored[position:]
    referenced |= {f"BTN-{n}" for n in _TICKET_REF.findall(tail)}

    unknown = sorted(referenced - tickets)
    assert unknown == [], f"authored docs cite unknown tickets: {unknown}"


def _backlog_statuses() -> dict[str, str]:
    return {t.id: t.status for t in load_tickets(BACKLOG)}


def test_complete_component_rows_only_cite_done_tickets():
    statuses = _backlog_statuses()
    component_table = re.compile(
        r"^\| `battalion[^|]+\|[^|]+\|(?P<status>[^|]+)\|$",
        re.MULTILINE,
    )
    rows = list(component_table.finditer(_status_text()))
    assert len(rows) >= 25, "component readiness table went missing"

    for row in rows:
        if "Complete" not in row.group("status"):
            continue
        cited: set[str] = set()
        for match in _EN_DASH_RANGE.finditer(row.group(0)):
            start, end = int(match.group(1)), int(match.group(2))
            cited.update(f"BTN-{n}" for n in range(start, end + 1))
        cited |= {
            f"BTN-{m.group(1)}"
            for m in re.finditer(r"BTN-(\d+)", row.group("status"))
        }
        not_done = sorted(
            ticket for ticket in cited if statuses.get(ticket) != "done"
        )
        assert not_done == [], (
            f"component row claims Complete citing unfinished tickets: "
            f"{not_done}"
        )


def test_readme_embeds_the_status_document():
    readme = README.read_text(encoding="utf-8")
    status_body = _status_text().strip()
    assert status_body in readme, (
        "README no longer embeds docs/status.md; run "
        "python scripts/sync_status.py"
    )
