"""Reviewer node (BTN-6).

Deliberately phase-agnostic: Reviewer's whole job is "copy the current
src/ tree into an isolated location, run the tests that exist there, and
report what happened." It never trusts a self-reported pass/fail from
Driver (there isn't one to trust — Driver just writes files), and it
doesn't know or care whether Driver is mid-RED or mid-GREEN. That's what
lets it slot into either checkpoint of a RED -> Reviewer -> GREEN ->
Refactorer loop without any Reviewer-side rework — see the conversation
that led to this design before BTN-6 was implemented.

Reviewer's declared write scope is always empty — it never writes files,
only reads (to build the clean copy) and calls the LLM to articulate a
rejection cause.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import build_write_tools
from battalion.state.models import RejectionRecord, RunState, RunStatus


@dataclass
class TestRunResult:
    __test__ = False  # tell pytest this isn't a test class despite the name
    passed: bool
    output: str
    returncode: int


class EmptyReviewContent(Exception):
    """Raised when the LLM returns empty/whitespace-only rejection-cause
    content. Without this check, an unusable cause string could be
    recorded, undermining same-cause-twice detection (interrupt trigger #1,
    built in BTN-8)."""


class SourceTreeMissing(Exception):
    """Raised when base_dir/src doesn't exist yet — Reviewer has nothing
    to copy or test. Without this check, shutil.copytree would raise a
    raw, unclear FileNotFoundError instead."""


def make_clean_copy(src_dir: str | Path) -> Path:
    """Copy src_dir into a fresh temporary directory, independent of the
    original — this is the "clean tree" in clean-tree re-verification."""
    dest = Path(tempfile.mkdtemp(prefix="battalion-clean-"))
    shutil.copytree(src_dir, dest, dirs_exist_ok=True)
    return dest


def run_tests_via_subprocess(clean_dir: str | Path) -> TestRunResult:
    """Run pytest in clean_dir via subprocess. This is the real, un-mocked
    default — Reviewer never takes Driver's word for pass/fail."""
    proc = subprocess.run(
        ["python", "-m", "pytest", "-q"],
        cwd=str(clean_dir),
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    return TestRunResult(passed=proc.returncode == 0, output=output, returncode=proc.returncode)


def run_reviewer(
    state: RunState,
    base_dir: str | Path,
    llm_config: NodeLLMConfig,
    call_llm_fn: Callable = call_llm,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    make_clean_copy_fn: Callable[[Path], Path] = make_clean_copy,
    run_tests_fn: Callable[[Path], TestRunResult] = run_tests_via_subprocess,
) -> RunState:
    """Run the Reviewer node: independently re-run tests from a clean copy
    of the current src/ tree, and return updated state — accepted (phase
    'done') or rejected (phase back to 'driver', rejection cause recorded).
    Raises WriteScopeMisconfigured or EmptyReviewContent on failure; never
    silently swallows either."""
    write_tools = build_write_tools("reviewer", state.write_scope, base_dir=base_dir)
    if write_tools:
        raise WriteScopeMisconfigured(
            "state.write_scope['reviewer'] must be empty — Reviewer never "
            f"writes files, but found tool entries: {sorted(write_tools.keys())}"
        )

    src_dir = Path(base_dir) / "src"
    if not src_dir.exists():
        raise SourceTreeMissing(
            f"No src/ directory found at {src_dir} — Reviewer has nothing to test."
        )

    clean_dir = make_clean_copy_fn(src_dir)
    try:
        result = run_tests_fn(clean_dir)
    finally:
        # Clean-tree copies are single-use scratch space — remove them so
        # repeated review runs don't leak directories under /tmp.
        shutil.rmtree(clean_dir, ignore_errors=True)

    if result.passed:
        return state.model_copy(update={"phase": "done", "status": RunStatus.DONE})

    resolved_prompt = system_prompt or load_system_prompt(
        "reviewer", prompts_dir=prompts_dir
    )
    messages = [
        {"role": "system", "content": resolved_prompt},
        {"role": "user", "content": f"Test output:\n{result.output}"},
    ]
    response = call_llm_fn("reviewer", llm_config, messages)
    cause = extract_content(response)

    if not cause or not cause.strip():
        raise EmptyReviewContent(
            "Reviewer LLM call returned empty rejection-cause content — "
            "refusing to record an unusable cause string."
        )

    cycle_number = len(state.reviewer_rejection_history) + 1
    new_history = state.reviewer_rejection_history + [
        RejectionRecord(cause=cause.strip(), cycle_number=cycle_number)
    ]
    return state.model_copy(update={
        "phase": "driver",
        "status": RunStatus.IN_PROGRESS,
        "reviewer_rejection_history": new_history,
    })
