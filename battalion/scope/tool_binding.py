"""Per-node write-scope tool binding (BTN-2, plan.md ADR-002).

A node's tool set is built here, before role execution, from its
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

import ntpath
from pathlib import Path, PureWindowsPath
from typing import Callable

class WriteScopeMisconfigured(Exception):
    """A declaration is unsafe or lacks a role's required write authority.

    This is operator configuration, never model output eligible for automatic
    correction or permission to fall back to a broader scope.
    """


def _unsafe_relative_path(path: str) -> bool:
    parts = path.split("/")
    # ntpath works on every host. Python 3.11/3.12 need pathlib's older
    # device-name check; avoid that deprecated API on newer Python versions.
    reserved = (
        ntpath.isreserved(path) if hasattr(ntpath, "isreserved") else
        any(PureWindowsPath(part).is_reserved() for part in parts)
    )
    return (
        not path or bool(PureWindowsPath(path).anchor) or ".." in parts
        or any(ord(char) < 32 or char in '<>:"|?*' for char in path)
        or reserved
        or any(part != "." and part.endswith((".", " ")) for part in parts)
    )


def _resolve_allow_missing(path: Path) -> Path:
    # New files/directories are valid roots, but other resolution failures
    # (notably symlink loops on newer Python versions) must not be suppressed
    # by non-strict resolution.
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        return path.resolve()


def normalize_scope_root(entry: str, base_dir: str | Path) -> Path:
    """Resolve a project-relative authority declaration, failing closed.

    Both separator styles have the same meaning on all supported hosts.
    Windows anchors/devices are rejected even on POSIX so moving a saved
    configuration between hosts cannot turn a relative root into an escape.
    No v1 role permits a grant of the project root itself.
    """
    normalized = entry.replace("\\", "/")
    if _unsafe_relative_path(normalized):
        raise WriteScopeMisconfigured(
            f"Invalid write scope {entry!r}: roots must be project-relative paths "
            "without anchors, parent traversal, or Windows path aliases."
        )
    try:
        base = _resolve_allow_missing(Path(base_dir))
        root = _resolve_allow_missing(base / normalized)
        if root == base or not root.is_relative_to(base):
            raise WriteScopeMisconfigured(
                f"Invalid write scope {entry!r}: root must resolve strictly within "
                f"the project ({base})."
            )
        return root
    except (OSError, RuntimeError, ValueError) as exc:
        raise WriteScopeMisconfigured(
            f"Cannot resolve write scope {entry!r} within the project: {exc}"
        ) from exc


def validate_write_scope(
    write_scope: dict[str, list[str]], base_dir: str | Path,
) -> dict[str, dict[str, Path]]:
    """Validate all declarations and return roots keyed by their original entries.

    Inactive phases and legacy entries are validated too. Binding consumes
    these results directly instead of resolving the selected entries again.
    """
    return {
        node: {entry: normalize_scope_root(entry, base_dir) for entry in entries}
        for node, entries in write_scope.items()
    }


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
        file_name: str,
    ):
        self._node_name = node_name
        self._root = root
        self._single_file = single_file
        self._on_violation = on_violation
        self._file_name = file_name

    def resolve(self, relative_path: str) -> Path:
        """Validate relative_path against this tool's declared root and
        return the resolved target path, without writing anything. Lets
        callers pre-validate a batch of paths before writing any of them."""
        try:
            # Authority is pinned at binding. A replaced directory, junction,
            # or single-file symlink cannot move that boundary after binding.
            if _resolve_allow_missing(self._root) != self._root:
                self._violate(relative_path)
            if self._single_file:
                if relative_path != self._file_name:
                    self._violate(relative_path)
                return self._root

            normalized = relative_path.replace("\\", "/")
            if _unsafe_relative_path(normalized):
                self._violate(relative_path)
            target = _resolve_allow_missing(self._root / normalized)
            if not target.is_relative_to(self._root):
                self._violate(relative_path)
            return target
        except (OSError, RuntimeError, ValueError):
            self._violate(relative_path)

    def write(self, relative_path: str, content: str) -> None:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Lazy import keeps the write-scope boundary independent of graph
        # construction while allowing BTN-19 to capture even idempotent writes.
        from battalion.execution import record_scoped_write

        record_scoped_write(target)

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
    # Never expose even an otherwise valid tool while another declaration is
    # invalid. In particular, invalid explicit phases cannot fall back.
    roots = validate_write_scope(write_scope, base_dir).get(node_name, {})

    tools: dict[str, _BoundWriteTool] = {}
    for entry, root in roots.items():
        normalized = entry.replace("\\", "/")
        is_dir = normalized.endswith("/")
        tools[entry] = _BoundWriteTool(
            node_name=node_name,
            root=root,
            single_file=not is_dir,
            on_violation=on_violation,
            file_name=Path(normalized).name,
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
    directory_tools = {
        key.replace("\\", "/"): tool for key, tool in write_tools.items()
        if not tool._single_file
    }
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
