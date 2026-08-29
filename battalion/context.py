"""Deterministic, bounded execution context for Battalion roles (BTN-26)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Sequence

from battalion.actors import format_actor_attribution
from battalion.intel.models import AcceptedInstinct
from battalion.state.models import InterventionDisposition, RunState

MAX_CONTEXT_CHARS = 32_000
MAX_FILE_CHARS = 8_000
MAX_INSTINCT_CONTEXT_CHARS = 6_000
MAX_INSTINCT_RECOMMENDATION_CHARS = 1_500
MAX_INSTINCT_APPLICABILITY_CHARS = 500
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


def _instinct_context(instincts: Sequence[AcceptedInstinct]) -> str | None:
    """Render whole, identified records into a dedicated bounded allowance."""

    chunks: list[str] = []
    used = 0
    for instinct in instincts:
        recommendation = instinct.recommendation[:MAX_INSTINCT_RECOMMENDATION_CHARS]
        applicability = instinct.applicability.description[
            :MAX_INSTINCT_APPLICABILITY_CHARS
        ]
        chunk = (
            f"### {instinct.instinct_id}\n"
            f"Recommendation: {recommendation}\n"
            f"Applicability: {applicability}\n"
            f"Tags: {', '.join(instinct.tags)}\n"
        )
        if used + len(chunk) > MAX_INSTINCT_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        used += len(chunk) + 1
    return "\n".join(chunks) if chunks else None


def _base_sections(
    state: RunState,
    instincts: Sequence[AcceptedInstinct],
) -> list[tuple[str, str]]:
    sections = [("Ticket", state.ticket_id[:500])]
    instinct_text = _instinct_context(instincts)
    if instinct_text is not None:
        sections.append(("Accepted Instincts", instinct_text))
    sections.append(("Specification", state.spec))
    return sections


def _human_intervention_context(
    state: RunState, target: str, node_execution_id: str | None
) -> str | None:
    if node_execution_id is None:
        return None
    delivered = [
        item for item in state.interventions
        if item.target.value == target
        and item.disposition is InterventionDisposition.DELIVERED
        and item.delivered_to_execution_id == node_execution_id
    ]
    if not delivered:
        return None
    return "\n\n".join(
        f"### {item.kind.value} ({item.action_id})\n"
        f"Actor: {format_actor_attribution(item.actor, item.actor_id)}\n"
        f"Target: {item.target.value}\n{item.text}"
        for item in delivered
    )


def architect_context(
    state: RunState,
    *,
    instincts: Sequence[AcceptedInstinct] = (),
    node_execution_id: str | None = None,
) -> str:
    sections = _base_sections(state, instincts)
    intervention = _human_intervention_context(
        state, "architect", node_execution_id
    )
    if intervention is not None:
        sections.append(("Human intervention", intervention))
    return _bounded(sections)


def driver_context(
    state: RunState,
    base_dir: str | Path,
    mode: Literal["red", "green"],
    *,
    instincts: Sequence[AcceptedInstinct] = (),
    node_execution_id: str | None = None,
    automatic_correction: str | None = None,
) -> str:
    selection = "implementation" if mode == "red" else "tests"
    file_kind = "Existing implementation" if mode == "red" else "Accepted RED tests"
    sections = _base_sections(state, instincts)
    intervention = _human_intervention_context(
        state, f"driver_{mode}", node_execution_id
    )
    if intervention is not None:
        sections.append(("Human intervention", intervention))
    if automatic_correction is not None:
        sections.append(("Battalion automatic correction", automatic_correction))
    sections.append(("Approved plan", _plan_text(base_dir)))
    sections.extend(
        (f"{file_kind}: {relative}", content)
        for relative, content in _driver_files(state, base_dir, selection)
    )
    return _bounded(sections)


def refactorer_context(
    state: RunState,
    base_dir: str | Path,
    *,
    instincts: Sequence[AcceptedInstinct] = (),
    node_execution_id: str | None = None,
) -> str:
    sections = _base_sections(state, instincts)
    intervention = _human_intervention_context(
        state, "refactorer", node_execution_id
    )
    if intervention is not None:
        sections.append(("Human intervention", intervention))
    authorized_paths = refactorer_authorized_paths(state)
    if authorized_paths:
        authorization = (
            "Only these production files were written by the accepted GREEN Driver "
            "attempt and may be changed:\n"
            + "\n".join(f"- {path}" for path in authorized_paths)
        )
    else:
        authorization = (
            "No GREEN Driver production artifact is recorded for this run. "
            "Return the explicit no-change result; do not create a file."
        )
    sections.append(("Authorized Refactorer targets", authorization))
    sections.append(("Approved plan", _plan_text(base_dir)))
    sections.extend(
        (f"Passing file: {relative}", content)
        for relative, content in _driver_files(state, base_dir, "all")
    )
    return _bounded(sections)


def reviewer_context(
    state: RunState,
    *,
    instincts: Sequence[AcceptedInstinct] = (),
) -> str:
    """Assemble Reviewer knowledge through the same canonical context path."""

    return _bounded(_base_sections(state, instincts))


def refactorer_authorized_paths(state: RunState) -> tuple[str, ...]:
    """Return the latest GREEN Driver artifacts that Refactorer may alter.

    Artifact provenance records files actually written by the preceding Driver
    attempt.  It is therefore a stronger boundary than a broad implementation
    root or a model's guess about authorship.
    """
    for execution in reversed(state.execution_record.node_executions):
        if execution.phase == "driver_green" and execution.outcome == "succeeded":
            return tuple(sorted(artifact.path for artifact in execution.artifact_provenance))
    return ()
