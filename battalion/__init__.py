"""Battalion — human-directed SDLC orchestration.

Public convenience exports are resolved lazily so importing a read-only
presentation adapter does not initialize graph or provider authority.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


__version__ = "1.0.0"

_EXPORTS = {
    "cli_main": ("battalion.cli", "main"),
    "load_config": ("battalion.config", "load_config"),
    "BattalionConfig": ("battalion.config", "BattalionConfig"),
    "build_graph": ("battalion.graph", "build_graph"),
    "run_ticket": ("battalion.graph", "run_ticket"),
    "resume_ticket": ("battalion.graph", "resume_ticket"),
    "ModelDiversityError": (
        "battalion.llm.litellm_client",
        "ModelDiversityError",
    ),
    "RunState": ("battalion.state.models", "RunState"),
    "RunStatus": ("battalion.state.models", "RunStatus"),
    "Budget": ("battalion.state.models", "Budget"),
    "CheckpointType": ("battalion.state.models", "CheckpointType"),
    "RejectionRecord": ("battalion.state.models", "RejectionRecord"),
    "save_state": ("battalion.state.persistence", "save_state"),
    "load_state": ("battalion.state.persistence", "load_state"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTS))
