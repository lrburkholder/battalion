"""Read-only Git-visible source collection for the artifact-target boundary."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
from uuid import UUID

from battalion.artifact_targets import normalize_target_path
from battalion.project_source import (
    ProjectSourceSnapshot, ProjectSourceUnavailable, SourceFileFingerprint,
)

MAX_SOURCE_FILES = 10000
MAX_SOURCE_FILE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 512 * 1024 * 1024


def _git(root: Path, *args: str, allow_unborn: bool = False) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        capture_output=True, timeout=15,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode and not (allow_unborn and result.returncode == 1):
        raise ProjectSourceUnavailable("Git could not read the selected project's source evidence.")
    if len(result.stdout) > 16 * 1024 * 1024:
        raise ProjectSourceUnavailable("Git source listing exceeds its bounded output limit.")
    return result.stdout


def _inventory(root: Path) -> tuple[str, set[str]]:
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", allow_unborn=True).decode("ascii").strip() or "unborn"
    paths = {
        raw.decode("utf-8") for raw in _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
        if raw
    }
    return head, {path for path in paths if path != ".battalion" and not path.startswith(".battalion/")}


def capture_project_source(
    project_root: str | Path,
    *,
    project_id: UUID | str,
    include_paths: tuple[str, ...] = (),
) -> ProjectSourceSnapshot:
    """Capture bounded file digests; never store contents or run Git hooks.

    ``include_paths`` keeps previously observed files and verified output paths
    visible even if Git ignore rules now hide them. Missing files stay absent
    so comparison detects deletions. Non-file entries (including submodules and
    directory symlinks) fail closed rather than receiving incomplete evidence.
    """
    try:
        root = Path(project_root).resolve(strict=True)
        git_root = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").strip())).resolve(strict=True)
        if root != git_root:
            raise ProjectSourceUnavailable("Source capture requires the selected project to be the Git worktree root.")
        head, visible = _inventory(root)
        paths = visible | set(include_paths)
        if len(paths) > MAX_SOURCE_FILES:
            raise ProjectSourceUnavailable("Source file count exceeds the snapshot limit.")
        files = []
        total = 0
        for path in sorted(paths):
            normalized = normalize_target_path(path)
            source = root / normalized
            # Resolve first: a dangling/escaping link must not look like an
            # ordinary absent tracked file and silently disappear.
            try:
                resolved = source.resolve(strict=True)
            except FileNotFoundError:
                if source.is_symlink():
                    raise ProjectSourceUnavailable(f"Dangling source link: {normalized}")
                continue
            relative = normalize_target_path(resolved.relative_to(root).as_posix())
            before = resolved.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ProjectSourceUnavailable(f"Source entry is not an exact regular file: {normalized}")
            if before.st_size > MAX_SOURCE_FILE_BYTES or total + before.st_size > MAX_SOURCE_TOTAL_BYTES:
                raise ProjectSourceUnavailable("Source bytes exceed the bounded snapshot policy.")
            with resolved.open("rb") as stream:
                contents = stream.read(MAX_SOURCE_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
            total += len(contents)
            if len(contents) > MAX_SOURCE_FILE_BYTES or total > MAX_SOURCE_TOTAL_BYTES:
                raise ProjectSourceUnavailable("Source bytes exceed the bounded snapshot policy.")
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or (
                source.resolve(strict=True) != resolved
            ):
                raise ProjectSourceUnavailable("Source changed while its snapshot was being read; retry collection.")
            files.append(SourceFileFingerprint(
                path=normalized, resolved_path=relative,
                sha256=hashlib.sha256(contents).hexdigest(), symlink=source.is_symlink(),
                executable=bool(before.st_mode & stat.S_IXUSR),
            ))
        if (head, visible) != _inventory(root):
            raise ProjectSourceUnavailable("Git source inventory changed during collection; retry collection.")
        return ProjectSourceSnapshot(project_id=project_id, head_revision=head, files=tuple(files))
    except ProjectSourceUnavailable:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise ProjectSourceUnavailable(f"Cannot collect complete project-source evidence: {type(exc).__name__}") from exc
