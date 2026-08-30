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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.execution import record_role_result
from battalion.nodes.errors import RoleContractViolation, RoleOutputError, WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import (
    build_write_tools,
    resolve_scoped_batch,
    scope_key_for_phase,
)
from battalion.state.models import InterruptLogEntry, RunState, RunStatus
from battalion.role_results import (
    RoleExecutionResult,
    RoleResultKind,
    RoleResultRejected,
    RoleResultSubmission,
    submit_role_result,
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)
_TEST_FILE_RE = re.compile(r"^test_.*\.py$|.*_test\.py$")
_ROLE_ESCALATION = "role-escalation"


class MalformedDriverOutput(RoleOutputError):
    """Raised when the LLM response isn't valid {"files": {...}} JSON."""


class EmptyDriverOutput(RoleOutputError):
    """Raised when the LLM returns a files dict with no entries. Without
    this check, the ticket would silently advance to 'reviewer' having
    written nothing."""


class InvalidModeOutput(RoleContractViolation):
    """Raised when a mode-scoped Driver call (BTN-11) produces files that
    violate what that mode is allowed to write: RED mode must only write
    test files, GREEN mode must not write any. Without this, mode is just
    a prompt suggestion an uncooperative LLM response can silently ignore —
    same reasoning as ADR-002's structural-over-trust write scope."""

    def __init__(
        self,
        message: str,
        *,
        offending_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            message,
            reason_code="driver-mode-artifact",
            offending_paths=offending_paths,
        )


@dataclass(frozen=True)
class DriverOutput:
    """Validated provider response before Battalion constructs a role result."""

    files: dict[str, str]
    result_submission: RoleResultSubmission | None = None


def _looks_like_test_file(relative_path: str) -> bool:
    return bool(_TEST_FILE_RE.match(Path(relative_path).name))


def extract_output(response: Any) -> DriverOutput:
    """Parse legacy file output or a semantic BTN-133 result submission."""
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
    raw_result = parsed.get("result")
    if raw_result is None:
        return DriverOutput(files=files)
    if not isinstance(raw_result, dict):
        raise MalformedDriverOutput("Driver 'result' must be a JSON object")
    try:
        submission = RoleResultSubmission.model_validate(raw_result)
    except ValueError as exc:
        raise MalformedDriverOutput(f"Driver result submission is invalid: {exc}") from exc
    if submission.kind is not RoleResultKind.COMPLETED_WITH_CHANGE and files:
        raise MalformedDriverOutput(
            f"{submission.kind.value} output must use an empty 'files' mapping"
        )
    return DriverOutput(files=files, result_submission=submission)


def extract_files(response: Any) -> dict[str, str]:
    """Backward-compatible helper returning only the validated file mapping."""
    return extract_output(response).files


def _route_non_mutating_result(
    state: RunState,
    mode: Literal["red", "green"],
    result: RoleExecutionResult,
) -> RunState:
    """Apply deterministic Driver routing; the role never selects a node."""
    resume_target = f"driver_{mode}"
    if result.kind is RoleResultKind.BLOCKED:
        return state.model_copy(update={
            "status": RunStatus.BLOCKED,
            "phase": resume_target,
            "resume_target": resume_target,
        })
    entry = InterruptLogEntry(
        trigger=_ROLE_ESCALATION,
        timestamp=datetime.now(timezone.utc),
        context={
            "role": "driver",
            "mode": mode,
            "role_result": result.model_dump(mode="json"),
            "next_phase": resume_target,
        },
    )
    return state.model_copy(update={
        "status": RunStatus.AWAITING_HUMAN,
        "interrupt_log": [*state.interrupt_log, entry],
        "phase": "awaiting_human",
        "resume_target": resume_target,
    })


def _record_result(result: RoleExecutionResult) -> None:
    """Persist a validated result, treating unbound evidence as bad model output."""

    try:
        record_role_result(result)
    except RoleResultRejected as exc:
        raise MalformedDriverOutput(str(exc)) from exc


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
        battalion/prompts/driver.md, no restriction on what files come back. Kept
        as the default so existing callers are unaffected.
      - "red": loads battalion/prompts/driver-red.md; every returned file must look
        like a test file (structurally enforced, not just prompted for).
      - "green": loads battalion/prompts/driver-green.md; no returned file may look
        like a test file.

    Raises InfraFailure, WriteScopeMisconfigured, MalformedDriverOutput,
    EmptyDriverOutput, InvalidModeOutput, or ScopeViolationError on
    failure — never silently swallows any of them."""
    if mode is not None and mode not in ("red", "green"):
        raise ValueError(f"mode must be 'red', 'green', or None, got {mode!r}")

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
    output = extract_output(response)
    files = output.files

    if output.result_submission is not None and (
        output.result_submission.kind is not RoleResultKind.COMPLETED_WITH_CHANGE
    ):
        if mode is None:
            raise MalformedDriverOutput(
                "Typed Driver non-mutating results require RED or GREEN mode"
            )
        try:
            result = submit_role_result(
                output.result_submission, role="driver", mode=mode
            )
        except RoleResultRejected as exc:
            raise MalformedDriverOutput(str(exc)) from exc
        _record_result(result)
        return _route_non_mutating_result(state, mode, result)

    if not files:
        raise EmptyDriverOutput(
            "Driver LLM call returned no files — refusing to advance the "
            "ticket to 'reviewer' having written nothing."
        )

    if output.result_submission is not None and mode is not None:
        # Reject a semantic contradiction before any scoped mutation occurs.
        try:
            submit_role_result(
                output.result_submission,
                role="driver",
                mode=mode,
                observed_artifact_count=1,
            )
        except RoleResultRejected as exc:
            raise MalformedDriverOutput(str(exc)) from exc

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

    if mode == "red":
        non_test_files = [p for p in files if not _looks_like_test_file(p)]
        if non_test_files:
            raise InvalidModeOutput(
                f"RED mode must only produce test files, got non-test "
                f"file(s): {non_test_files}",
                offending_paths=tuple(non_test_files),
            )
    elif mode == "green":
        test_files = [p for p in files if _looks_like_test_file(p)]
        if test_files:
            raise InvalidModeOutput(
                f"GREEN mode must not produce test files, got: {test_files}",
                offending_paths=tuple(test_files),
            )

    try:
        targets = resolve_scoped_batch(write_tools, list(files))
    except ValueError as exc:
        raise WriteScopeMisconfigured(str(exc)) from exc
    for (tool, relative_path), content in zip(targets, files.values(), strict=True):
        tool.write(relative_path, content)

    if mode is not None:
        submission = output.result_submission or RoleResultSubmission(
            kind=RoleResultKind.COMPLETED_WITH_CHANGE
        )
        try:
            result = submit_role_result(
                submission,
                role="driver",
                mode=mode,
                observed_artifact_count=len(targets),
            )
        except RoleResultRejected as exc:
            raise MalformedDriverOutput(str(exc)) from exc
        _record_result(result)

    return state.model_copy(update={
        "phase": "reviewer",
        "status": RunStatus.IN_PROGRESS,
    })
