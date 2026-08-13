"""Deterministic, bounded execution context for Battalion roles (BTN-26)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from battalion.state.models import RunState

MAX_CONTEXT_CHARS = 32_000
MAX_FILE_CHARS = 8_000
_TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".json", ".jsx", ".kt", ".md", ".php", ".py",
    ".rb", ".rs", ".sh", ".sql", ".swift", ".toml", ".ts", ".tsx",
    ".txt", ".xml", ".yaml", ".yml",
}


def _is_test_file(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or "tests" in path.parts


def _driver_files(
    state: RunState,
    base_dir: str | Path,
    selection: Literal["tests", "implementation", "all"],
) -> list[tuple[str, str]]:
    """Read eligible files inside phase-appropriate roots in stable order."""
    base = Path(base_dir).resolve()
    candidates: dict[str, Path] = {}
    if selection == "tests":
        keys = ["driver_red"] if "driver_red" in state.write_scope else ["driver"]
    elif selection == "implementation":
        keys = ["driver_green"] if "driver_green" in state.write_scope else ["driver"]
    else:
        keys = [
            key
            for key in ("driver_red", "driver_green", "refactorer")
            if key in state.write_scope
        ]
        if not keys:
            keys = ["driver"]
    scopes = {scope for key in keys for scope in state.write_scope.get(key, [])}
    for scope in sorted(scopes):
        root = (base / scope).resolve()
        if not root.is_relative_to(base) or not root.exists():
            continue
        paths = root.rglob("*") if root.is_dir() else (root,)
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_relative_to(base):
                continue
            is_test = _is_test_file(path.relative_to(base))
            if selection == "tests" and not is_test:
                continue
            if selection == "implementation" and is_test:
                continue
            relative = path.relative_to(base).as_posix()
            candidates[relative] = path

    files: list[tuple[str, str]] = []
    for relative in sorted(candidates):
        try:
            content = candidates[relative].read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n[truncated]"
        files.append((relative, content))
    return files


def _plan_text(base_dir: str | Path) -> str:
    base = Path(base_dir).resolve()
    path = (base / "plan.md").resolve()
    if not path.is_relative_to(base):
        return "[No approved plan is available.]"
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "[No approved plan is available.]"


def _bounded(sections: list[tuple[str, str]]) -> str:
    chunks: list[str] = []
    remaining = MAX_CONTEXT_CHARS
    for heading, content in sections:
        chunk = f"## {heading}\n{content.strip()}\n"
        if len(chunk) > remaining:
            marker = "\n[context truncated]"
            if remaining > len(marker):
                chunks.append(chunk[: remaining - len(marker)] + marker)
            break
        chunks.append(chunk)
        remaining -= len(chunk) + 1
        if remaining <= 0:
            break
    return "\n".join(chunks)


def architect_context(state: RunState) -> str:
    return _bounded([
        ("Ticket", state.ticket_id),
        ("Specification", state.spec),
    ])


def driver_context(
    state: RunState,
    base_dir: str | Path,
    mode: Literal["red", "green"],
) -> str:
    selection = "implementation" if mode == "red" else "tests"
    file_kind = "Existing implementation" if mode == "red" else "Accepted RED tests"
    sections = [
        ("Ticket", state.ticket_id),
        ("Specification", state.spec),
        ("Approved plan", _plan_text(base_dir)),
    ]
    sections.extend(
        (f"{file_kind}: {relative}", content)
        for relative, content in _driver_files(state, base_dir, selection)
    )
    return _bounded(sections)


def refactorer_context(state: RunState, base_dir: str | Path) -> str:
    sections = [
        ("Ticket", state.ticket_id),
        ("Specification", state.spec),
        ("Approved plan", _plan_text(base_dir)),
    ]
    sections.extend(
        (f"Passing file: {relative}", content)
        for relative, content in _driver_files(state, base_dir, "all")
    )
    return _bounded(sections)
