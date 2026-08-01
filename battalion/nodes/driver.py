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
from pathlib import Path
from typing import Any, Callable

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import build_write_tools
from battalion.state.models import RunState, RunStatus

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


class MalformedDriverOutput(Exception):
    """Raised when the LLM response isn't valid {"files": {...}} JSON."""


class EmptyDriverOutput(Exception):
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
) -> RunState:
    """Run the Driver node: produce test + implementation files from
    ticket_text, write them under the declared 'src/' scope, and return
    updated state. Raises InfraFailure, WriteScopeMisconfigured,
    MalformedDriverOutput, EmptyDriverOutput, or ScopeViolationError on
    failure — never silently swallows any of them."""
    write_tools = build_write_tools(
        "driver", state.write_scope, base_dir=base_dir, on_violation=on_violation
    )
    if "src/" not in write_tools:
        raise WriteScopeMisconfigured(
            "state.write_scope['driver'] has no 'src/' entry — "
            "the Driver node cannot write its output."
        )

    resolved_prompt = system_prompt or load_system_prompt(
        "driver", prompts_dir=prompts_dir
    )
    messages = [
        {"role": "system", "content": resolved_prompt},
        {"role": "user", "content": ticket_text},
    ]

    response = call_llm_fn("driver", llm_config, messages)
    files = extract_files(response)

    if not files:
        raise EmptyDriverOutput(
            "Driver LLM call returned no files — refusing to advance the "
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
