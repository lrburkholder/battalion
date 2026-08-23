"""Generate Battalion's status documentation from the canonical backlog.

``backlog.json`` owns ticket identity and status. ``docs/status.md`` is the
single human-readable status view; the "Delivered work" region inside it is
mechanically regenerated here, and the whole document is spliced into
``README.md`` between marker comments so GitHub renders one embedded copy.

Run ``python scripts/sync_status.py`` after changing backlog status, or
``python scripts/sync_status.py --check`` (used by tests and CI) to fail on
drift instead of writing.

When the ticket source later moves behind the WorkSource abstraction
(BTN-71/BTN-72, ADR-0025), this script's input swaps to that provider; its
output contract does not change.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

STATUS_DOC = "docs/status.md"
README = "README.md"

GENERATED_START = "<!-- BEGIN GENERATED:backlog-delivery"
GENERATED_END = "<!-- END GENERATED:backlog-delivery -->"
GENERATED_NOTICE = (
    "<!-- BEGIN GENERATED:backlog-delivery "
    "(regenerate with: python scripts/sync_status.py) -->"
)

README_START = "<!-- battalion:status:start -->"
README_END = "<!-- battalion:status:end -->"

STATUS_ICONS = {
    "done": "yes",
    "in-progress": "wip",
    "blocked": "blocked",
    "in-review": "review",
    "cancelled": "cancelled",
}

_TICKET_NUMBER = re.compile(r"^BTN-(\d+)$")


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    status: str


def load_tickets(backlog_path: Path) -> list[Ticket]:
    """Read the canonical backlog into ordered tickets."""
    import json

    payload = json.loads(backlog_path.read_text(encoding="utf-8"))
    tickets = [
        Ticket(id=item["id"], title=item["title"], status=item["status"])
        for item in payload["items"]
    ]
    return sorted(tickets, key=_sort_key)


def _sort_key(ticket: Ticket) -> tuple[int, str]:
    match = _TICKET_NUMBER.match(ticket.id)
    return ((int(match.group(1)) if match else 10**6), ticket.id)


def _row(ticket: Ticket, symbol: str) -> str:
    title = ticket.title.replace("|", "\\|")
    return f"| {ticket.id} | {title} | {symbol} |"


def render_delivery_section(tickets: list[Ticket]) -> str:
    """Render the generated backlog-delivery markdown region."""
    buckets: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        if ticket.status != "not-started":
            buckets.setdefault(ticket.status, []).append(ticket)

    lines = [f"{GENERATED_NOTICE}", ""]
    lines.append("### Shipped")
    lines.append("")
    lines.append("| Ticket | Title | Status |")
    lines.append("| --- | --- | --- |")
    lines.extend(_row(t, "Yes") for t in buckets.get("done", []))

    lines.append("")
    lines.append("### In flight")
    lines.append("")
    lines.append("| Ticket | Title | Status |")
    lines.append("| --- | --- | --- |")
    inflight = [
        *(buckets.get("in-review", [])),
        *(buckets.get("in-progress", [])),
        *(buckets.get("blocked", [])),
    ]
    if inflight:
        lines.extend(
            _row(t, STATUS_ICONS[t.status].capitalize()) for t in inflight
        )
    else:
        lines.append("| — | Nothing currently in flight | — |")

    cancelled = buckets.get("cancelled", [])
    if cancelled:
        lines.append("")
        lines.append("### Cancelled")
        lines.append("")
        lines.append("| Ticket | Title | Status |")
        lines.append("| --- | --- | --- |")
        lines.extend(_row(t, "No") for t in cancelled)

    planned = sum(1 for t in tickets if t.status == "not-started")
    lines.append("")
    lines.append(
        f"Ticket scope, dependencies, acceptance criteria, and the "
        f"{planned} planned (not-started) tickets live in the canonical "
        f"[backlog.json](backlog.json)."
    )
    lines.append("")
    lines.append(GENERATED_END)
    return "\n".join(lines)


def _replace_between(text: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end), re.DOTALL
    )
    if not pattern.search(text):
        raise ValueError(f"markers not found: {start!r}")
    return pattern.sub(lambda _: replacement, text, count=1)


def sync_documents(root: Path = REPOSITORY_ROOT, check: bool = False) -> list[str]:
    """Regenerate status regions; return paths whose content would change."""
    backlog_path = root / "backlog.json"
    status_path = root / STATUS_DOC
    readme_path = root / README

    tickets = load_tickets(backlog_path)
    delivery = render_delivery_section(tickets)

    changed: list[str] = []

    status_text = status_path.read_text(encoding="utf-8")
    new_status = _replace_between(
        status_text, GENERATED_START, GENERATED_END, delivery
    )
    if new_status != status_text:
        changed.append(STATUS_DOC)

    # The README embeds the status document verbatim between its markers.
    status_body = new_status.strip()
    readme_text = readme_path.read_text(encoding="utf-8")
    new_readme = _replace_between(
        readme_text,
        README_START,
        README_END,
        f"{README_START}\n\n{status_body}\n\n{README_END}",
    )
    if new_readme != readme_text:
        changed.append(README)

    if check:
        return changed

    if STATUS_DOC in changed:
        status_path.write_text(new_status, encoding="utf-8", newline="\n")
    if README in changed:
        readme_path.write_text(new_readme, encoding="utf-8", newline="\n")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without writing; exit 1 when documents are stale",
    )
    args = parser.parse_args()
    stale = sync_documents(REPOSITORY_ROOT, check=args.check)
    if stale:
        names = ", ".join(stale)
        if args.check:
            parser.exit(1, f"stale status documentation: {names}\n")
        print(f"updated: {names}")
    else:
        print("status documentation is current")


if __name__ == "__main__":
    main()
