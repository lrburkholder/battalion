"""Architect node (BTN-4) — the first LLM-driven node in the graph.

Given a spec-level input, validates a typed handoff candidate and writes a
human-readable projection to plan.md.
The node builds its own write tools internally from state.write_scope
(battalion.scope.tool_binding.build_write_tools) rather than receiving
pre-built tools from a caller — this means the node's own code never has
the opportunity to even request a scope beyond its own declared entry,
which is the strongest form of ADR-002's guarantee.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from battalion.artifact_targets import ArchitectHandoffCandidate
from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.nodes.errors import RoleContractViolation, WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import build_write_tools
from battalion.state.models import RunState, RunStatus


class EmptyPlanContent(Exception):
    """Raised when the LLM returns empty/whitespace-only content. Without
    this check, an empty plan.md would be written and the ticket would
    silently advance to 'driver' as if the plan succeeded."""


class InvalidArchitectHandoff(RoleContractViolation):
    """A provider candidate failed the typed pre-write Architect contract."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        offending_paths: tuple[str, ...] = (),
    ) -> None:
        # Graph evidence bounds detail at 2,000 characters. Pydantic error
        # rendering may include the rejected input, so bound it before the
        # exception reaches the durable correction path.
        detail = message if len(message) <= 1_900 else message[:1_897] + "..."
        super().__init__(
            detail,
            reason_code=reason_code,
            offending_paths=offending_paths[:10],
        )


def _reject_duplicate_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _offending_paths(value: object) -> tuple[str, ...]:
    """Extract bounded raw path evidence without treating it as authority."""
    if not isinstance(value, dict) or not isinstance(value.get("targets"), list):
        return ()
    paths: list[str] = []
    for target in value["targets"][:10]:
        if not isinstance(target, dict):
            continue
        path = target.get("project_relative_path")
        if isinstance(path, str):
            paths.append(path[:1_000])
    return tuple(paths)


def parse_handoff(content: str) -> ArchitectHandoffCandidate:
    """Parse and validate the complete provider candidate before any write."""
    if len(content) > 262_144:
        raise InvalidArchitectHandoff(
            "Architect output exceeds the 262144-character response bound",
            reason_code="architect-handoff-malformed",
        )
    try:
        raw = json.loads(content, object_pairs_hook=_reject_duplicate_json_fields)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidArchitectHandoff(
            f"Architect output is not unambiguous JSON: {exc}",
            reason_code="architect-handoff-malformed",
        ) from exc
    try:
        return ArchitectHandoffCandidate.model_validate(raw)
    except ValidationError as exc:
        raise InvalidArchitectHandoff(
            f"Architect handoff candidate is invalid: {exc}",
            reason_code="architect-handoff-invalid",
            offending_paths=_offending_paths(raw),
        ) from exc


def render_plan(candidate: ArchitectHandoffCandidate) -> str:
    """Render a human projection; the validated candidate remains authoritative."""
    lines = [
        candidate.plan_markdown.rstrip(),
        "",
        "<!-- BEGIN GENERATED:artifact-targets -->",
        "## Validated artifact targets",
        "",
        "| Target ID | Project-relative path | Owner | Phase | Operation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for target in candidate.targets:
        path = html.escape(target.project_relative_path)
        for assignment in target.assignments:
            lines.append(
                f"| `{target.target_id}` | <code>{path}</code> | "
                f"{assignment.owner_role} | {assignment.workflow_phase.value} | "
                f"{assignment.intended_operation} |"
            )
    lines.extend(["", "### Target-referenced implementation steps", ""])
    for number, step in enumerate(candidate.implementation_steps, start=1):
        target_ids = ", ".join(f"`{target_id}`" for target_id in step.target_ids)
        lines.append(f"{number}. {step.description}  ")
        lines.append(f"   Targets: {target_ids}")
    lines.extend(["", "<!-- END GENERATED:artifact-targets -->", ""])
    return "\n".join(lines)


def run_architect(
    state: RunState,
    spec_text: str,
    llm_config: NodeLLMConfig,
    base_dir: str | Path = ".",
    call_llm_fn: Callable = call_llm,
    on_violation: Callable[[dict], None] | None = None,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    on_stream: Callable[[dict], None] | None = None,
) -> RunState:
    """Run the Architect node: validate a typed handoff from spec_text, render
    its plan projection to plan.md, and return updated state. Raises
    InfraFailure (from call_llm_fn), InvalidArchitectHandoff, or
    WriteScopeMisconfigured on failure — never silently swallows them.

    system_prompt, if not given, is loaded from battalion/prompts/architect.md (or
    prompts_dir, if overridden) — see battalion.prompts.loader. Passing
    system_prompt directly is mainly for tests; production callers should
    rely on the packaged asset. Prompt edits remain role-contract changes
    subject to architectural review."""
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
    if on_stream is not None:
        response = call_llm_fn("architect", llm_config, messages, on_stream=on_stream)
    else:
        response = call_llm_fn("architect", llm_config, messages)
    plan_content = extract_content(response)

    if not isinstance(plan_content, str):
        raise InvalidArchitectHandoff(
            "Architect output content must be text",
            reason_code="architect-handoff-malformed",
        )
    if not plan_content or not plan_content.strip():
        raise EmptyPlanContent(
            "Architect LLM call returned empty content — refusing to write "
            "an empty plan.md or advance the ticket to 'driver'."
        )

    candidate = parse_handoff(plan_content)
    rendered_plan = render_plan(candidate)

    write_tools["plan.md"].write("plan.md", rendered_plan)

    return state.model_copy(update={
        "phase": "driver",
        "status": RunStatus.IN_PROGRESS,
    })
