"""Refactorer node (BTN-13, plan.md ADR-008).

New node, same shape as Driver: builds its own scoped write tools
internally, but calls build_write_tools with node_name='driver' (not
'refactorer') since it shares Driver's declared src/ write_scope entry
rather than getting its own. Refactors passing code without changing
behavior; re-checked by Reviewer with expect_pass=True.

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
from battalion.scope.tool_binding import build_write_tools
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
    """Run the Refactorer node: refactor passing code without changing
    behavior, write refactored files under src/, and return updated state.

    Per ADR-008, this node uses node_name='driver' when calling
    build_write_tools, so it shares Driver's declared write_scope entry
    (typically 'src/') rather than requiring its own 'refactorer' key.

    Raises InfraFailure (from call_llm_fn), WriteScopeMisconfigured,
    MalformedRefactorerOutput, EmptyRefactorerOutput on failure — never
    silently swallows any of them."""
    # ADR-008: use "driver" as node_name to share its write_scope entry
    write_tools = build_write_tools(
        "driver", state.write_scope, base_dir=base_dir, on_violation=on_violation
    )
    if "src/" not in write_tools:
        raise WriteScopeMisconfigured(
            "state.write_scope['driver'] has no 'src/' entry — "
            "the Refactorer node cannot write its output."
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

    tool = write_tools["src/"]
    # Pre-validate every path before writing any of them, so a scope
    # violation on file N doesn't leave files 1..N-1 written to disk.
    for relative_path in files:
        tool.resolve(relative_path)
    for relative_path, content in files.items():
        tool.write(relative_path, content)

    return state.model_copy(update={
        "phase": "reviewer",
        "status": RunStatus.IN_PROGRESS,
    })
