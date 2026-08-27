"""Generate status documentation from canonical GitHub Issues.

GitHub retrieval is deliberately separate from issue normalization and
rendering, so unit tests can use deterministic fixtures without credentials or
network access.  Run this script to update projections; use ``--check`` to
report drift without writing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STATUS_DOC = "docs/status.md"
README = "README.md"
GENERATED_START = "<!-- BEGIN GENERATED:backlog-delivery"
GENERATED_END = "<!-- END GENERATED:backlog-delivery -->"
GENERATED_NOTICE = "<!-- BEGIN GENERATED:backlog-delivery (regenerate with: python scripts/sync_status.py) -->"
README_START = "<!-- battalion:status:start -->"
README_END = "<!-- battalion:status:end -->"
STATUS_ICONS = {"done": "yes", "in-progress": "wip", "blocked": "blocked", "in-review": "review", "cancelled": "cancelled"}

_TICKET_NUMBER = re.compile(r"^BTN-(\d+)$")
_METADATA_BLOCK = re.compile(r"^## Battalion metadata\s*```(?:yaml|yml)?\s*(?P<body>.*?)```", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_TITLE_PREFIX = re.compile(r"^BTN-\d+\s+[—-]\s+")
_PHASES = frozenset({"architecture", "design", "implementation", "research", "rollout", "testing"})
_PRIORITIES = frozenset({"P0", "P1", "P2", "P3", "unknown"})
_ROLES = frozenset({"architect", "driver", "reviewer", "unknown"})
_ACTIVE_STATUS_LABELS = {"status:in-progress": "in-progress", "status:blocked": "blocked", "status:in-review": "in-review"}


class IssueNormalizationError(ValueError):
    """A Battalion Issue fails the locked Issue Schema v1 contract."""


@dataclass(frozen=True)
class Ticket:
    """Normalized Issue Schema v1 data used by deterministic rendering."""

    id: str
    title: str
    status: str
    phase: str
    priority: str
    assignee_role: str


def fetch_github_issues(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[dict[str, Any]]:
    """Retrieve raw records through the sole GitHub transport boundary."""
    command = ["gh", "issue", "list", "--state", "all", "--limit", "1000", "--json", "number,title,body,state,stateReason,labels"]
    completed = runner(command, check=True, capture_output=True, text=True, encoding="utf-8")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise ValueError("GitHub issue retrieval returned a non-list JSON payload")
    return payload


def _metadata(body: object, issue_number: object) -> dict[str, str] | None:
    if not isinstance(body, str):
        return None
    match = _METADATA_BLOCK.search(body)
    if match is None:
        return None
    parsed: dict[str, str] = {}
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise IssueNormalizationError(f"GitHub issue #{issue_number}: malformed Battalion metadata line {line!r}")
        parsed[key.strip()] = value.strip().strip("\"'")
    return parsed


def _label_names(issue: Mapping[str, object]) -> set[str]:
    labels = issue.get("labels", [])
    if not isinstance(labels, list):
        raise IssueNormalizationError(f"GitHub issue #{issue.get('number', '?')}: labels must be a list")
    names: set[str] = set()
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, Mapping) and isinstance(label.get("name"), str):
            names.add(label["name"])
        else:
            raise IssueNormalizationError(f"GitHub issue #{issue.get('number', '?')}: malformed label {label!r}")
    return names


def _one_label(labels: set[str], prefix: str, issue_number: object) -> str | None:
    matches = sorted(label for label in labels if label.startswith(prefix))
    if len(matches) > 1:
        raise IssueNormalizationError(f"GitHub issue #{issue_number}: multiple {prefix} labels: {matches}")
    return matches[0] if matches else None


def _validate_classifications(metadata: Mapping[str, str], labels: set[str], issue_number: object) -> None:
    expected_keys = {"schema_version", "ticket_id", "phase", "priority", "assignee_role"}
    unknown_keys, missing_keys = sorted(set(metadata) - expected_keys), sorted(expected_keys - set(metadata))
    if unknown_keys or missing_keys or metadata.get("schema_version") != "1":
        raise IssueNormalizationError(f"GitHub issue #{issue_number}: Issue Schema v1 metadata requires exactly {sorted(expected_keys)} (missing={missing_keys}, unknown={unknown_keys}, schema_version={metadata.get('schema_version')!r})")
    if _TICKET_NUMBER.fullmatch(metadata["ticket_id"]) is None:
        raise IssueNormalizationError(f"GitHub issue #{issue_number}: invalid ticket_id {metadata['ticket_id']!r}")
    if metadata["phase"] not in _PHASES:
        raise IssueNormalizationError(f"GitHub issue #{issue_number}: unsupported phase {metadata['phase']!r}")
    if metadata["priority"] not in _PRIORITIES:
        raise IssueNormalizationError(f"GitHub issue #{issue_number}: unsupported priority {metadata['priority']!r}")
    if metadata["assignee_role"] not in _ROLES:
        raise IssueNormalizationError(f"GitHub issue #{issue_number}: unsupported assignee_role {metadata['assignee_role']!r}")
    for key, prefix in (("phase", "phase:"), ("priority", "priority:"), ("assignee_role", "role:")):
        label, value = _one_label(labels, prefix, issue_number), metadata[key]
        if value == "unknown":
            if label is not None:
                raise IssueNormalizationError(f"GitHub issue #{issue_number}: {key}=unknown must not have {label!r}")
        elif label != f"{prefix}{value}":
            raise IssueNormalizationError(f"GitHub issue #{issue_number}: metadata {key}={value!r} disagrees with label {label!r}; expected {prefix + value!r}")


def _lifecycle_status(issue: Mapping[str, object], labels: set[str]) -> str:
    number, state, reason = issue.get("number", "?"), issue.get("state"), issue.get("stateReason")
    status_labels = sorted(label for label in labels if label.startswith("status:"))
    if state == "OPEN":
        if not status_labels:
            return "not-started"
        if len(status_labels) == 1 and status_labels[0] in _ACTIVE_STATUS_LABELS:
            return _ACTIVE_STATUS_LABELS[status_labels[0]]
        raise IssueNormalizationError(f"GitHub issue #{number}: unsupported open lifecycle labels {status_labels}")
    if state == "CLOSED":
        if status_labels:
            raise IssueNormalizationError(f"GitHub issue #{number}: closed issues cannot retain status labels {status_labels}")
        if reason == "COMPLETED":
            return "done"
        if reason == "NOT_PLANNED":
            return "cancelled"
        raise IssueNormalizationError(f"GitHub issue #{number}: unsupported closed lifecycle reason {reason!r}")
    raise IssueNormalizationError(f"GitHub issue #{number}: unsupported issue state {state!r}")


def normalize_issues(issues: Iterable[Mapping[str, object]]) -> list[Ticket]:
    """Validate and normalize raw GitHub Issues without network access.

    Issues without Battalion metadata are unrelated and ignored.  A malformed
    metadata block fails loudly, preventing a second interpretation of schema
    fields in the renderer.
    """
    tickets: list[Ticket] = []
    seen_ids: set[str] = set()
    for issue in issues:
        number = issue.get("number", "?")
        metadata = _metadata(issue.get("body"), number)
        if metadata is None:
            title = issue.get("title")
            if isinstance(title, str) and _TITLE_PREFIX.match(title):
                raise IssueNormalizationError(
                    f"GitHub issue #{number}: Battalion ticket title requires Issue Schema v1 metadata"
                )
            continue
        labels = _label_names(issue)
        _validate_classifications(metadata, labels, number)
        ticket_id = metadata["ticket_id"]
        if ticket_id in seen_ids:
            raise IssueNormalizationError(f"GitHub issue #{number}: duplicate Battalion ticket ID {ticket_id}")
        title = issue.get("title")
        if not isinstance(title, str) or not title.strip():
            raise IssueNormalizationError(f"GitHub issue #{number}: title must be a non-empty string")
        seen_ids.add(ticket_id)
        tickets.append(Ticket(ticket_id, _TITLE_PREFIX.sub("", title.strip()), _lifecycle_status(issue, labels), metadata["phase"], metadata["priority"], metadata["assignee_role"]))
    return sorted(tickets, key=_sort_key)


def load_tickets(issues: Iterable[Mapping[str, object]]) -> list[Ticket]:
    """Normalize the canonical GitHub Issue data into renderable tickets."""
    return normalize_issues(issues)


def _sort_key(ticket: Ticket) -> tuple[int, str]:
    match = _TICKET_NUMBER.match(ticket.id)
    return (int(match.group(1)) if match else 10**6, ticket.id)


def _row(ticket: Ticket, symbol: str) -> str:
    escaped_title = ticket.title.replace("|", "\\|")
    return f"| {ticket.id} | {escaped_title} | {symbol} |"


def render_delivery_section(tickets: Sequence[Ticket]) -> str:
    """Render the generated status region as a pure deterministic function."""
    buckets: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        if ticket.status != "not-started":
            buckets.setdefault(ticket.status, []).append(ticket)
    lines = [GENERATED_NOTICE, "", "### Shipped", "", "| Ticket | Title | Status |", "| --- | --- | --- |"]
    lines.extend(_row(ticket, "Yes") for ticket in buckets.get("done", []))
    lines.extend(["", "### In flight", "", "| Ticket | Title | Status |", "| --- | --- | --- |"])
    inflight = [*buckets.get("in-review", []), *buckets.get("in-progress", []), *buckets.get("blocked", [])]
    if inflight:
        lines.extend(_row(ticket, STATUS_ICONS[ticket.status].capitalize()) for ticket in inflight)
    else:
        lines.append("| — | Nothing currently in flight | — |")
    cancelled = buckets.get("cancelled", [])
    if cancelled:
        lines.extend(["", "### Cancelled", "", "| Ticket | Title | Status |", "| --- | --- | --- |"])
        lines.extend(_row(ticket, "No") for ticket in cancelled)
    planned = sum(ticket.status == "not-started" for ticket in tickets)
    lines.extend(["", f"Ticket scope, dependencies, and acceptance criteria live in the canonical [GitHub Issues](https://github.com/lrburkholder/battalion/issues) ({planned} planned/not-started).", "", GENERATED_END])
    return "\n".join(lines)


def _replace_between(text: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"markers not found: {start!r}")
    return pattern.sub(lambda _: replacement, text, count=1)


IssueReader = Callable[[], list[dict[str, Any]]]


def sync_documents(root: Path = REPOSITORY_ROOT, check: bool = False, issue_reader: IssueReader = fetch_github_issues) -> list[str]:
    """Regenerate status projections; return paths whose content would change."""
    tickets = load_tickets(issue_reader())
    delivery = render_delivery_section(tickets)
    status_path, readme_path = root / STATUS_DOC, root / README
    status_text = status_path.read_text(encoding="utf-8")
    new_status = _replace_between(status_text, GENERATED_START, GENERATED_END, delivery)
    readme_text = readme_path.read_text(encoding="utf-8")
    new_readme = _replace_between(readme_text, README_START, README_END, f"{README_START}\n\n{new_status.strip()}\n\n{README_END}")
    changed = [path for path, before, after in ((STATUS_DOC, status_text, new_status), (README, readme_text, new_readme)) if before != after]
    if not check:
        if STATUS_DOC in changed:
            status_path.write_text(new_status, encoding="utf-8", newline="\n")
        if README in changed:
            readme_path.write_text(new_readme, encoding="utf-8", newline="\n")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report projection drift without writing")
    args = parser.parse_args()
    stale = sync_documents(check=args.check)
    if stale:
        if args.check:
            parser.exit(1, f"stale status documentation: {', '.join(stale)}\n")
        print(f"updated: {', '.join(stale)}")
    else:
        print("status documentation is current")


if __name__ == "__main__":
    main()
