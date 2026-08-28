"""Refactorer node (BTN-13, ADR-0008, ADR-0013).

The Refactorer builds scoped write tools from its explicit phase entry when
present. Legacy configurations share Driver's implementation scope. It
refactors passing code without changing behavior and is re-checked by Reviewer.

The key distinction from Driver: this node is called after GREEN_check
passes, and its sole job is to improve the code's structure/clarity without
altering functionality.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from battalion.context import refactorer_authorized_paths
from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.execution import record_no_change
from battalion.nodes.errors import RoleOutputError, WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import (
    build_write_tools,
    resolve_scoped_batch,
    scope_key_for_phase,
)
from battalion.state.models import RunState, RunStatus

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


class MalformedRefactorerOutput(RoleOutputError):
    """Raised when the LLM response isn't valid {files: {...}} JSON."""


class EmptyRefactorerOutput(RoleOutputError):
    """Raised when the LLM returns an unexplained empty files mapping.

    A Refactorer no-op is valid only when it explicitly identifies the
    ``no-change`` outcome and supplies a short reason.
    """


class UnauthorizedRefactorerOutput(RoleOutputError):
    """Raised when Refactorer tries to alter a non-GREEN artifact.

    Refactorer's write scope is a structural filesystem boundary.  This
    narrower contract boundary prevents the role from expanding a ticket by
    creating tests, documentation, or edits to unrelated project code.
    """


@dataclass(frozen=True)
class RefactorerOutput:
    """Validated structured response from the Refactorer provider."""

    files: dict[str, str]
    no_change_reason: str | None = None


def extract_output(response: Any) -> RefactorerOutput:
    """Parse Refactorer's files result or explicit behavior-preserving no-op."""
    if isinstance(response, dict):
        raw_content = response["choices"][0]["message"]["content"]
    else:
        raw_content = response.choices[0].message.content

    fence_match = _FENCE_RE.match(raw_content.strip())
    json_text = fence_match.group(1) if fence_match else raw_content

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise MalformedRefactorerOutput(
            f"Refactorer LLM output was not valid JSON: {exc}"
        ) from exc

    if not isinstance(parsed, dict) or "files" not in parsed:
        raise MalformedRefactorerOutput(
            "Refactorer LLM output JSON must have a top-level 'files' key"
        )

    files = parsed["files"]
    if not isinstance(files, dict):
        raise MalformedRefactorerOutput(
            f"'files' must be a JSON object of path -> content, got {type(files).__name__}"
        )
    for path, content in files.items():
        if not isinstance(path, str) or not path.strip():
            raise MalformedRefactorerOutput(
                f"'files' keys must be non-empty path strings, got {path!r}"
            )
        if not isinstance(content, str):
            raise MalformedRefactorerOutput(
                f"'files' values must be strings, got {type(content).__name__} for {path!r}"
            )
    outcome = parsed.get("outcome", "changed")
    if outcome not in {"changed", "no-change"}:
        raise MalformedRefactorerOutput(
            "Refactorer LLM output 'outcome' must be 'changed' or 'no-change'"
        )

    reason = parsed.get("reason")
    if outcome == "no-change":
        if files:
            raise MalformedRefactorerOutput(
                "Refactorer 'no-change' output must use an empty 'files' mapping"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise EmptyRefactorerOutput(
                "Refactorer 'no-change' output requires a concise non-empty reason"
            )
        if len(reason) > 500:
            raise MalformedRefactorerOutput(
                "Refactorer 'no-change' reason must be at most 500 characters"
            )
        return RefactorerOutput(files={}, no_change_reason=reason.strip())

    if not files:
        raise EmptyRefactorerOutput(
            "Refactorer LLM returned no files without an explicit 'no-change' outcome"
        )
    return RefactorerOutput(files=files)


def extract_files(response: Any) -> dict[str, str]:
    """Backward-compatible helper returning only validated changed files."""
    return extract_output(response).files


def _is_nonproduction_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", maxsplit=1)[-1].casefold()
    suffix = Path(name).suffix.casefold()
    return (
        normalized.startswith("docs/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or suffix in {".md", ".rst", ".txt"}
    )


def _validate_authorized_targets(
    state: RunState,
    targets: list[tuple[Any, str]],
    base_dir: str | Path,
) -> None:
    """Reject Refactorer output outside the latest GREEN artifact set.

    A direct node-level caller without any execution evidence remains
    backwards-compatible.  Every graph execution has this evidence, so its
    Refactorer attempt is constrained to the GREEN Driver's actual writes.
    """
    if not state.execution_record.node_executions:
        return

    authorized = set(refactorer_authorized_paths(state))
    root = Path(base_dir).resolve()
    requested = {
        tool.resolve(relative_path).resolve().relative_to(root).as_posix()
        for tool, relative_path in targets
    }
    prohibited = sorted(path for path in requested if _is_nonproduction_path(path))
    unexpected = sorted(path for path in requested if path not in authorized)
    if prohibited or unexpected:
        details: list[str] = []
        if prohibited:
            details.append("non-production paths: " + ", ".join(prohibited))
        if unexpected:
            details.append("not written by accepted GREEN Driver: " + ", ".join(unexpected))
        raise UnauthorizedRefactorerOutput(
            "Refactorer may modify only production artifacts written by the accepted "
            "GREEN Driver attempt; " + "; ".join(details)
        )


def run_refactorer(
    state: RunState,
    refactor_text: str,
    llm_config: NodeLLMConfig,
    base_dir: str | Path = ".",
    call_llm_fn: Callable = call_llm,
    on_violation: Callable[[dict], None] | None = None,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    on_stream: Callable[[dict], None] | None = None,
) -> RunState:
    """Run Refactorer and write output through its implementation roots.

    Per ADR-0013, an explicit ``refactorer`` scope wins. Legacy configurations
    fall back to Driver's scope per ADR-0008.

    An explicit ``{\"outcome\": \"no-change\", \"files\": {}, \"reason\": ...}``
    is a successful behavior-preserving result: it records the decision and
    proceeds to review without writes. Other empty mappings are rejected.

    Raises InfraFailure (from call_llm_fn), WriteScopeMisconfigured,
    MalformedRefactorerOutput, EmptyRefactorerOutput, or
    UnauthorizedRefactorerOutput on failure — never silently swallows any of
    them."""
    scope_key = scope_key_for_phase(state.write_scope, "refactorer")
    write_tools = build_write_tools(
        scope_key, state.write_scope, base_dir=base_dir, on_violation=on_violation
    )
    if not write_tools:
        raise WriteScopeMisconfigured(
            f"state.write_scope[{scope_key!r}] declares no write roots — "
            "Refactorer cannot write its output."
        )

    resolved_prompt = system_prompt or load_system_prompt(
        "refactorer", prompts_dir=prompts_dir
    )
    messages = [
        {"role": "system", "content": resolved_prompt},
        {"role": "user", "content": refactor_text},
    ]

    if on_stream is not None:
        response = call_llm_fn("refactorer", llm_config, messages, on_stream=on_stream)
    else:
        response = call_llm_fn("refactorer", llm_config, messages)
    output = extract_output(response)
    files = output.files

    if output.no_change_reason is not None:
        record_no_change(output.no_change_reason)
        return state.model_copy(update={
            "phase": "reviewer",
            "status": RunStatus.IN_PROGRESS,
        })

    try:
        targets = resolve_scoped_batch(write_tools, list(files))
    except ValueError as exc:
        raise WriteScopeMisconfigured(str(exc)) from exc
    _validate_authorized_targets(state, targets, base_dir)
    for (tool, relative_path), content in zip(targets, files.values(), strict=True):
        tool.write(relative_path, content)

    return state.model_copy(update={
        "phase": "reviewer",
        "status": RunStatus.IN_PROGRESS,
    })
