"""Architect node (BTN-4) — the first LLM-driven node in the graph.

Given a spec-level input, produces plan content and writes it to plan.md.
The node builds its own write tools internally from state.write_scope
(battalion.scope.tool_binding.build_write_tools) rather than receiving
pre-built tools from a caller — this means the node's own code never has
the opportunity to even request a scope beyond its own declared entry,
which is the strongest form of ADR-002's guarantee.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import build_write_tools
from battalion.state.models import RunState, RunStatus


class EmptyPlanContent(Exception):
    """Raised when the LLM returns empty/whitespace-only content. Without
    this check, an empty plan.md would be written and the ticket would
    silently advance to 'driver' as if the plan succeeded."""


def run_architect(
    state: RunState,
    spec_text: str,
    llm_config: NodeLLMConfig,
    base_dir: str | Path = ".",
    call_llm_fn: Callable = call_llm,
    on_violation: Callable[[dict], None] | None = None,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
) -> RunState:
    """Run the Architect node: produce a plan from spec_text, write it to
    plan.md, and return updated state. Raises InfraFailure (from call_llm_fn)
    or WriteScopeMisconfigured on failure — never silently swallows either.

    system_prompt, if not given, is loaded from prompts/architect.md (or
    prompts_dir, if overridden) — see battalion.prompts.loader. Passing
    system_prompt directly is mainly for tests; production callers should
    rely on the file so prompt iteration stays a config change."""
    write_tools = build_write_tools(
        "architect", state.write_scope, base_dir=base_dir, on_violation=on_violation
    )
    if "plan.md" not in write_tools:
        raise WriteScopeMisconfigured(
            "state.write_scope['architect'] has no 'plan.md' entry — "
            "the Architect node cannot write its output."
        )

    resolved_prompt = system_prompt or load_system_prompt(
        "architect", prompts_dir=prompts_dir
    )
    messages = [
        {"role": "system", "content": resolved_prompt},
        {"role": "user", "content": spec_text},
    ]

    # Any failure here (including InfraFailure after exhausted retries)
    # propagates to the caller uncaught — BTN-8 routes InfraFailure to
    # interrupt trigger #5 at the graph level, not here.
    response = call_llm_fn("architect", llm_config, messages)
    plan_content = extract_content(response)

    if not plan_content or not plan_content.strip():
        raise EmptyPlanContent(
            "Architect LLM call returned empty content — refusing to write "
            "an empty plan.md or advance the ticket to 'driver'."
        )

    write_tools["plan.md"].write("plan.md", plan_content)

    return state.model_copy(update={
        "phase": "driver",
        "status": RunStatus.IN_PROGRESS,
    })
