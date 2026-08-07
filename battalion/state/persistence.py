"""Local JSON persistence for RunState, following regiment-backlog.json's
schema_version convention (spec.md, findings.md)."""
from __future__ import annotations

import json
from pathlib import Path

from battalion.state.models import RunState


def save_state(state: RunState, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_state(path: str | Path) -> RunState:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No state file at {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"State file at {path} is not valid JSON: {exc}") from exc

    # Malformed shape (missing/invalid fields) surfaces as ValidationError,
    # not a silent pass — required by BTN-1 acceptance criteria.
    return RunState.model_validate(raw)
