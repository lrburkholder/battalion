"""Validate Battalion ticket metadata in a pull-request body before merge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from battalion.ticket_lifecycle import TicketLifecycleError, ensure_no_declared_auto_close


def _event_pr_body(event_path: str) -> str | None:
    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise TicketLifecycleError("GitHub event does not contain a pull_request object")
    body = pull_request.get("body")
    if body is not None and not isinstance(body, str):
        raise TicketLifecycleError("pull request body must be text or null")
    return body


def main() -> None:
    body = _event_pr_body(os.environ["GITHUB_EVENT_PATH"])
    ensure_no_declared_auto_close(body)


if __name__ == "__main__":
    try:
        main()
    except (TicketLifecycleError, KeyError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"pull request ticket metadata validation failed: {exc}") from exc
