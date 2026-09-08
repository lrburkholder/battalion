"""Read-only artifact-target inspection using existing structural scope tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from battalion.artifact_targets import ArtifactTargetContract, normalize_target_path
from battalion.artifact_target_reconciliation import ArtifactTargetPathInspection
from battalion.artifact_target_state import ArtifactTargetReasonCode as Reason
from battalion.scope.tool_binding import (
    ScopeViolationError, WriteScopeMisconfigured, _resolve_allow_missing,
    build_write_tools, scope_key_for_phase, validate_write_scope,
)
from battalion.workflow_recipes import WorkflowStage


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def inspect_artifact_target_paths(
    contract: ArtifactTargetContract | None,
    *,
    write_scope: dict[str, list[str]],
    base_dir: str | Path,
    case_sensitive_paths: bool,
) -> ArtifactTargetPathInspection:
    """Inspect exact paths without writing, invoking a model, or widening scope.

    Scope identity is conservative: declarations are sorted/deduplicated but
    retain their spelling, trailing slash, explicit empty phases and role keys.
    Path identity pins policy and project-relative resolutions (including scope
    roots), not file contents or existence. Creating an intended artifact does
    not by itself invalidate this snapshot; retargeting a symlink does.
    """
    if not isinstance(case_sensitive_paths, bool):
        raise TypeError("case_sensitive_paths must be an explicit boolean policy")
    reasons: set[Reason] = set()
    scope_digest = _digest({
        "version": "1.0",
        "declarations": {role: sorted(set(entries)) for role, entries in write_scope.items()},
    })
    resolutions: dict[str, str] = {}
    root_resolutions: dict[str, dict[str, str]] = {}
    checked: list[str] = []
    try:
        base = Path(base_dir).resolve(strict=True)
        if not base.is_dir():
            raise ValueError("project root must be a directory")
        roots = validate_write_scope(write_scope, base)
        root_resolutions = {
            role: {entry: root.relative_to(base).as_posix() for entry, root in entries.items()}
            for role, entries in roots.items()
        }
    except WriteScopeMisconfigured:
        reasons.add(Reason.OUT_OF_SCOPE)
        base = None
    except (OSError, RuntimeError, ValueError):
        reasons.add(Reason.UNSAFE_PATH)
        base = None

    seen_paths: set[str] = set()
    seen_resolved: set[str] = set()
    phase_keys = {
        WorkflowStage.ARCHITECTURE: "architect",
        WorkflowStage.DRIVER_RED: "driver_red",
        WorkflowStage.DRIVER_GREEN: "driver_green",
        WorkflowStage.REFACTOR: "refactorer",
    }
    tools_by_key: dict = {}
    for target in contract.targets if contract else ():
        checked.append(target.target_id)
        path = target.project_relative_path
        path_key = path if case_sensitive_paths else path.casefold()
        if path_key in seen_paths:
            reasons.add(Reason.DUPLICATE_TARGETS)
        seen_paths.add(path_key)
        if base is None:
            continue
        try:
            resolved = _resolve_allow_missing(base / path)
            relative = resolved.relative_to(base).as_posix()
            # Lexical safety must also hold after following a symlink. An
            # in-project alias to .git or .battalion is still forbidden.
            normalize_target_path(relative)
            if resolved.is_dir():
                raise ValueError("target must be an exact file")
            ancestor = resolved.parent
            while ancestor != base:
                if ancestor.exists() and not ancestor.is_dir():
                    raise ValueError("target parent must be a directory")
                ancestor = ancestor.parent
            resolutions[target.target_id] = relative
            resolved_key = relative if case_sensitive_paths else relative.casefold()
            if resolved_key in seen_resolved:
                reasons.add(Reason.DUPLICATE_TARGETS)
            seen_resolved.add(resolved_key)
        except (OSError, RuntimeError, ValueError):
            reasons.add(Reason.UNSAFE_PATH)
            continue
        for assignment in target.assignments:
            phase = phase_keys[assignment.workflow_phase]
            key = (phase if phase == "architect" else scope_key_for_phase(write_scope, phase))
            try:
                if key not in tools_by_key:
                    tools_by_key[key] = build_write_tools(key, write_scope, base)
                authorized = False
                for entry, tool in tools_by_key[key].items():
                    normalized = entry.replace("\\", "/")
                    root = PurePosixPath(normalized).as_posix()
                    candidate = path
                    compare_root = root if case_sensitive_paths else root.casefold()
                    compare_path = path if case_sensitive_paths else path.casefold()
                    if normalized.endswith("/"):
                        if not compare_path.startswith(compare_root + "/"):
                            continue
                        candidate = path[len(root) + 1:]
                    elif compare_path != compare_root:
                        continue
                    else:
                        candidate = PurePosixPath(normalized).name
                    try:
                        if tool.resolve(candidate) == resolved:
                            authorized = True
                            break
                    except ScopeViolationError:
                        continue
                if not authorized:
                    reasons.add(Reason.OUT_OF_SCOPE)
            except WriteScopeMisconfigured:
                reasons.add(Reason.OUT_OF_SCOPE)
    return ArtifactTargetPathInspection(
        contract_id=contract.contract_id if contract else None,
        checked_target_ids=tuple(checked), write_scope_digest=scope_digest,
        path_policy_digest=_digest({
            "version": "1.0", "case_sensitive_paths": case_sensitive_paths,
            "symlinks": "contained-exact-files", "scope_roots": root_resolutions,
            "target_resolutions": resolutions,
        }),
        case_sensitive_paths=case_sensitive_paths,
        reason_codes=tuple(sorted(reasons, key=lambda reason: reason.value)),
    )
