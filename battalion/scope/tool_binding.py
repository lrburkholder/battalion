"""Per-node write-scope tool binding (BTN-2, plan.md ADR-002).

A node's tool set is built here, at graph-construction time, from its
declared entries in RunState.write_scope. A node is only ever given tool
objects bound to its own declared paths — there is no shared/central tool
capable of writing anywhere else, so a cross-node violation has no tool to
even attempt (structural enforcement, per ADR-002).

Within a single declared root (e.g. a directory like "src/"), a local
traversal guard is unavoidable — arbitrary filenames under that root can't
be enumerated as separate tools. That guard is scoped to the one root it's
bound to, which is the distinction ADR-002 draws against a central guard
checking every write against a global scope table.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


class ScopeViolationError(Exception):
    """Raised when a node's bound tool is asked to write outside the single
    root it was constructed for."""


class _BoundWriteTool:
    """A write tool bound to exactly one declared scope entry for one node.
    Cannot be reused across nodes or across other scope entries."""

    def __init__(
        self,
        node_name: str,
        root: Path,
        single_file: bool,
        on_violation: Callable[[dict], None] | None,
    ):
        self._node_name = node_name
        self._root = root
        self._single_file = single_file
        self._on_violation = on_violation

    def resolve(self, relative_path: str) -> Path:
        """Validate relative_path against this tool's declared root and
        return the resolved target path, without writing anything. Lets
        callers pre-validate a batch of paths before writing any of them."""
        if self._single_file:
            if relative_path != self._root.name:
                self._violate(relative_path)
            return self._root

        if Path(relative_path).is_absolute():
            self._violate(relative_path)
        target = (self._root / relative_path).resolve()
        root_resolved = self._root.resolve()
        if root_resolved not in target.parents and target != root_resolved:
            self._violate(relative_path)
        return target

    def write(self, relative_path: str, content: str) -> None:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _violate(self, attempted_path: str) -> None:
        if self._on_violation is not None:
            self._on_violation(
                {
                    "node": self._node_name,
                    "attempted_path": attempted_path,
                    "root": str(self._root),
                }
            )
        raise ScopeViolationError(
            f"node '{self._node_name}' attempted to write "
            f"'{attempted_path}' outside its declared scope ({self._root})"
        )


def build_write_tools(
    node_name: str,
    write_scope: dict[str, list[str]],
    base_dir: str | Path = ".",
    on_violation: Callable[[dict], None] | None = None,
) -> dict[str, _BoundWriteTool]:
    """Build the write-tool set for one node from its declared scope.

    Returns a dict keyed by the declared scope entry (e.g. "plan.md" or
    "src/") -> a tool bound only to that entry. Entries belonging to other
    nodes never appear here at all.
    """
    base_dir = Path(base_dir)
    entries = write_scope.get(node_name, [])

    tools: dict[str, _BoundWriteTool] = {}
    for entry in entries:
        is_dir = entry.endswith("/")
        root = base_dir / entry
        tools[entry] = _BoundWriteTool(
            node_name=node_name,
            root=root,
            single_file=not is_dir,
            on_violation=on_violation,
        )
    return tools


def scope_key_for_phase(
    write_scope: dict[str, list[str]],
    phase_key: str,
    legacy_key: str = "driver",
) -> str:
    """Choose a phase-specific scope without weakening legacy runs.

    An explicitly declared phase entry wins even when it is empty. Falling
    back only when the entry is absent lets existing ``driver: [src/]``
    configurations keep working while allowing RED, GREEN, and Refactorer to
    receive genuinely distinct tool sets.
    """
    return phase_key if phase_key in write_scope else legacy_key


def resolve_scoped_batch(
    write_tools: dict[str, _BoundWriteTool],
    relative_paths: list[str],
) -> list[tuple[_BoundWriteTool, str]]:
    """Resolve output paths to their phase-bound tools atomically.

    With one declared root, legacy root-relative output remains valid. A
    root-qualified path is also accepted. With multiple roots, qualification
    is required so the target is unambiguous. No tool outside ``write_tools``
    can be selected.
    """
    directory_tools = {key: tool for key, tool in write_tools.items() if key.endswith("/")}
    if not directory_tools:
        raise ValueError("a writing phase requires at least one declared directory root")

    resolved: list[tuple[_BoundWriteTool, str]] = []
    for supplied_path in relative_paths:
        normalized = supplied_path.replace("\\", "/")
        if Path(supplied_path).is_absolute() or normalized.startswith("/"):
            # Any bound tool can report the same structural violation; it
            # still cannot write beyond its own root.
            next(iter(directory_tools.values())).resolve(supplied_path)

        matches = [root for root in directory_tools if normalized.startswith(root)]
        if matches:
            root = max(matches, key=len)
            path_within_root = normalized[len(root):]
            if not path_within_root:
                directory_tools[root].resolve("../")
            resolved.append((directory_tools[root], path_within_root))
            continue

        if len(directory_tools) != 1:
            # Route through a bound tool's local guard so the normal
            # violation audit callback fires as well as the hard block.
            next(iter(directory_tools.values())).resolve(f"../{normalized}")
        tool = next(iter(directory_tools.values()))
        resolved.append((tool, supplied_path))

    # Validate the complete batch before the caller performs any write.
    for tool, path_within_root in resolved:
        tool.resolve(path_within_root)
    return resolved
