"""Driver node (BTN-5).

SCOPE NOTE: this ticket's acceptance criteria ask for scoped multi-file
output from a single LLM call, not an actual executed red-green-refactor
cycle. A real RGR loop (write a failing test, run it, confirm red, write
implementation, run again, confirm green, optionally refactor) would mean
actually executing pytest in a sandbox and feeding results back to the LLM
across multiple calls — a materially bigger capability than what's built
here. This was flagged before implementation began; the system prompt asks
the LLM to *reason* in RGR terms and produce both test and implementation
files, but nothing here verifies the tests it wrote actually fail-then-pass.
Real test execution is a deferred capability, not silently assumed to be
covered by this ticket.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Literal

from battalion.execution import record_unchanged_test_echo
from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.nodes.errors import RoleOutputError, WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import (
    build_write_tools,
    resolve_scoped_batch,
    scope_key_for_phase,
)
from battalion.state.models import RunState, RunStatus

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)
_TEST_FILE_RE = re.compile(r"^test_.*\.py$|.*_test\.py$")


class MalformedDriverOutput(RoleOutputError):
    """Raised when the LLM response isn't valid {"files": {...}} JSON."""


class EmptyDriverOutput(RoleOutputError):
    """Raised when the LLM returns a files dict with no entries. Without
    this check, the ticket would silently advance to 'reviewer' having
    written nothing."""


class InvalidModeOutput(RoleOutputError):
    """Raised when a mode-scoped Driver call (BTN-11) produces files that
    violate what that mode is allowed to write: RED mode must only write
    test files, GREEN mode must not write any. Without this, mode is just
    a prompt suggestion an uncooperative LLM response can silently ignore —
    same reasoning as ADR-002's structural-over-trust write scope."""


def _looks_like_test_file(relative_path: str) -> bool:
    return bool(_TEST_FILE_RE.match(Path(relative_path).name))


def _safe_relative_path(path: str) -> str | None:
    """Return a normalized project-relative path, or ``None`` if unsafe."""
    normalized = path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        candidate.is_absolute()
        or PureWindowsPath(path).is_absolute()
        or any(part in {".", ".."} for part in candidate.parts)
    ):
        return None
    return candidate.as_posix()


def _latest_red_artifacts(state: RunState) -> dict[str, str]:
    """Return the artifact digest for the most recent successful RED attempt."""
    for execution in reversed(state.execution_record.node_executions):
        if execution.phase == "driver_red" and execution.outcome == "succeeded":
            return {artifact.path: artifact.sha256 for artifact in execution.artifact_provenance}
    return {}


def _candidate_workspace_paths(
    returned_path: str, green_scope_entries: list[str]
) -> set[str]:
    """Map a GREEN output key to its possible project-relative identities.

    A single GREEN root permits root-relative output (``test_x.py``), while
    models also commonly return a workspace-relative key (``src/test_x.py``).
    This helper only creates candidates for safe relative paths; it never
    resolves or writes the model-supplied path.
    """
    relative = _safe_relative_path(returned_path)
    if relative is None:
        return set()
    candidates = {relative}
    for root in green_scope_entries:
        if not root.endswith("/"):
            continue
        normalized_root = _safe_relative_path(root.rstrip("/"))
        if normalized_root is not None:
            candidates.add(f"{normalized_root}/{relative}")
    return candidates


def _same_text_except_transport_newlines(returned: str, recorded: str) -> bool:
    """Allow only CRLF/LF transport differences and one final newline.

    Whitespace inside a file can be behaviorally meaningful (and Python source
    is indentation-sensitive), so this intentionally does not strip or
    otherwise normalize it.
    """
    def normalize(value: str) -> str:
        value = value.replace("\r\n", "\n")
        return value[:-1] if value.endswith("\n") else value

    return normalize(returned) == normalize(recorded)


def _unchanged_red_echoes(
    files: dict[str, str],
    state: RunState,
    base_dir: str | Path,
    green_scope_entries: list[str],
) -> set[str]:
    """Find GREEN test entries that exactly repeat accepted RED artifacts.

    The on-disk artifact must still match the RED provenance digest. This keeps
    the exception read-only and prevents a later manual test edit from being
    silently treated as a model echo.
    """
    red_artifacts = _latest_red_artifacts(state)
    if not red_artifacts:
        return set()

    root = Path(base_dir).resolve()
    ignored: set[str] = set()
    for returned_path, returned_content in files.items():
        if not _looks_like_test_file(returned_path):
            continue
        matching_paths = (
            _candidate_workspace_paths(returned_path, green_scope_entries)
            & red_artifacts.keys()
        )
        for artifact_path in matching_paths:
            target = (root / artifact_path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                continue
            recorded_bytes = target.read_bytes()
            if sha256(recorded_bytes).hexdigest() != red_artifacts[artifact_path]:
                continue
            try:
                recorded_content = recorded_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if _same_text_except_transport_newlines(returned_content, recorded_content):
                ignored.add(returned_path)
                break
    return ignored


def extract_files(response: Any) -> dict[str, str]:
    """Extract a {relative_path: content} mapping from a litellm-style
    response. Accepts either plain JSON or a single markdown-fenced JSON
    block, since LLMs commonly wrap JSON output in fences despite
    instructions not to."""
    if isinstance(response, dict):
        raw_content = response["choices"][0]["message"]["content"]
    else:
        raw_content = response.choices[0].message.content

    fence_match = _FENCE_RE.match(raw_content.strip())
    json_text = fence_match.group(1) if fence_match else raw_content

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise MalformedDriverOutput(
            f"Driver LLM output was not valid JSON: {exc}"
        ) from exc

    if not isinstance(parsed, dict) or "files" not in parsed:
        raise MalformedDriverOutput(
            "Driver LLM output JSON must have a top-level 'files' key"
        )

    files = parsed["files"]
    if not isinstance(files, dict):
        raise MalformedDriverOutput(
            f"'files' must be a JSON object of path -> content, got {type(files).__name__}"
        )
    for path, content in files.items():
        if not isinstance(path, str) or not path.strip():
            raise MalformedDriverOutput(
                f"'files' keys must be non-empty path strings, got {path!r}"
            )
        if not isinstance(content, str):
            raise MalformedDriverOutput(
                f"'files' values must be strings, got {type(content).__name__} for {path!r}"
            )
    return files


def run_driver(
    state: RunState,
    ticket_text: str,
    llm_config: NodeLLMConfig,
    base_dir: str | Path = ".",
    call_llm_fn: Callable = call_llm,
    on_violation: Callable[[dict], None] | None = None,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    mode: Literal["red", "green"] | None = None,
    on_stream: Callable[[dict], None] | None = None,
) -> RunState:
    """Run the Driver node and write output through its phase-bound roots.

    mode (BTN-11, ADR-006), if given, must be "red" or "green":
      - None (default): original BTN-5 combined behavior — loads
        prompts/driver.md, no restriction on what files come back. Kept
        as the default so existing callers are unaffected.
      - "red": loads prompts/driver-red.md; every returned file must look
        like a test file (structurally enforced, not just prompted for).
      - "green": loads prompts/driver-green.md; no returned file may look
        like a test file.

    Raises InfraFailure, WriteScopeMisconfigured, MalformedDriverOutput,
    EmptyDriverOutput, InvalidModeOutput, or ScopeViolationError on
    failure — never silently swallows any of them."""
    if mode is not None and mode not in ("red", "green"):
        raise ValueError(f"mode must be 'red', 'green', or None, got {mode!r}")

    phase_scope_key = "driver" if mode is None else f"driver_{mode}"
    scope_key = scope_key_for_phase(state.write_scope, phase_scope_key)
    write_tools = build_write_tools(
        scope_key, state.write_scope, base_dir=base_dir, on_violation=on_violation
    )
    if not write_tools:
        raise WriteScopeMisconfigured(
            f"state.write_scope[{scope_key!r}] declares no write roots — "
            f"Driver {mode or 'combined'} cannot write its output."
        )

    prompt_node_name = "driver" if mode is None else f"driver-{mode}"
    resolved_prompt = system_prompt or load_system_prompt(
        prompt_node_name, prompts_dir=prompts_dir
    )
    messages = [
        {"role": "system", "content": resolved_prompt},
        {"role": "user", "content": ticket_text},
    ]

    if on_stream is not None:
        response = call_llm_fn("driver", llm_config, messages, on_stream=on_stream)
    else:
        response = call_llm_fn("driver", llm_config, messages)
    files = extract_files(response)

    if mode == "green":
        ignored_test_echoes = _unchanged_red_echoes(
            files,
            state,
            base_dir,
            state.write_scope.get(scope_key, []),
        )
        for path in ignored_test_echoes:
            record_unchanged_test_echo(path)
        files = {
            path: content for path, content in files.items()
            if path not in ignored_test_echoes
        }

    if not files:
        raise EmptyDriverOutput(
            "Driver LLM call returned no writable files — refusing to advance "
            "the ticket to 'reviewer' having written nothing."
        )

    if mode == "red":
        non_test_files = [p for p in files if not _looks_like_test_file(p)]
        if non_test_files:
            raise InvalidModeOutput(
                f"RED mode must only produce test files, got non-test "
                f"file(s): {non_test_files}"
            )
    elif mode == "green":
        test_files = [p for p in files if _looks_like_test_file(p)]
        if test_files:
            raise InvalidModeOutput(
                f"GREEN mode must not produce test files, got: {test_files}"
            )

    try:
        targets = resolve_scoped_batch(write_tools, list(files))
    except ValueError as exc:
        raise WriteScopeMisconfigured(str(exc)) from exc
    for (tool, relative_path), content in zip(targets, files.values(), strict=True):
        tool.write(relative_path, content)

    return state.model_copy(update={
        "phase": "reviewer",
        "status": RunStatus.IN_PROGRESS,
    })
