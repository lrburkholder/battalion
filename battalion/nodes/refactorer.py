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
from pathlib import Path
from typing import Any, Callable

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import (
    build_write_tools,
    resolve_scoped_batch,
    scope_key_for_phase,
)
from battalion.state.models import RunState, RunStatus

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


class MalformedRefactorerOutput(Exception):
    """Raised when the LLM response isn't valid {files: {...}} JSON."""


class EmptyRefactorerOutput(Exception):
    """Raised when the LLM returns a files dict with no entries. Without
    this check, the ticket would silently advance to 'reviewer' having
    written nothing."""


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
    return files


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

    Raises InfraFailure (from call_llm_fn), WriteScopeMisconfigured,
    MalformedRefactorerOutput, EmptyRefactorerOutput on failure — never
    silently swallows any of them."""
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
    files = extract_files(response)

    if not files:
        raise EmptyRefactorerOutput(
            "Refactorer LLM call returned no files — refusing to advance the "
            "ticket to 'reviewer' having written nothing."
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
