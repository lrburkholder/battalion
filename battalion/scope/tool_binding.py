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

    def write(self, relative_path: str, content: str) -> None:
        if self._single_file:
            target = self._root
            if relative_path != self._root.name:
                self._violate(relative_path)
        else:
            if Path(relative_path).is_absolute():
                self._violate(relative_path)
                return  # pragma: no cover — _violate always raises
            target = (self._root / relative_path).resolve()
            root_resolved = self._root.resolve()
            if root_resolved not in target.parents and target != root_resolved:
                self._violate(relative_path)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

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
