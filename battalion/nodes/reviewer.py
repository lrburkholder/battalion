"""Reviewer node (BTN-6, extended in BTN-12).

Reviewer's core mechanism is unchanged from BTN-6: copy the current src/
tree into an isolated location, re-run the tests that exist there via
subprocess, never trust a self-reported pass/fail. What BTN-12 adds is
checkpoint-awareness (ADR-007, ADR-009): which outcome counts as "accept"
depends on the checkpoint being reviewed --

  RED_CHECK:      accept means tests FAIL (the feature genuinely doesn't
                   exist yet) -- accepting means advancing to Driver(GREEN)
  GREEN_CHECK:     accept means tests PASS -- advancing to Refactorer
  REFACTOR_CHECK:  accept means tests still PASS after refactoring --
                   advancing to 'done'

This corrects a real bug caught during the architecture pass before BTN-7:
BTN-6's original always-pass-is-accept logic would have silently rejected
every correctly-written RED-check test.

Reviewer's declared write scope is always empty — it never writes files,
only reads (to build the clean copy) and calls the LLM to articulate a
rejection cause.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.scope.tool_binding import build_write_tools
from battalion.state.models import CheckpointType, RejectionRecord, RunState, RunStatus

# expect_pass derived from checkpoint (ADR-007) -- one source of truth,
# rather than a separate expect_pass param a caller could set
# inconsistently with the checkpoint (e.g. RED_CHECK + expect_pass=True).
_EXPECT_PASS_BY_CHECKPOINT: dict[CheckpointType, bool] = {
    CheckpointType.RED_CHECK: False,
    CheckpointType.GREEN_CHECK: True,
    CheckpointType.REFACTOR_CHECK: True,
}

# Phase to move to when the checkpoint's expectation is met (accept).
_NEXT_PHASE_ON_ACCEPT: dict[CheckpointType, str] = {
    CheckpointType.RED_CHECK: "driver_green",  # correctly-failing test confirmed -> do GREEN
    CheckpointType.GREEN_CHECK: "refactorer",  # tests pass -> refactor step
    CheckpointType.REFACTOR_CHECK: "done",     # still passes after refactor -> complete
}

# Phase to retry when the checkpoint's expectation is NOT met (reject).
_RETRY_PHASE_ON_REJECT: dict[CheckpointType, str] = {
    CheckpointType.RED_CHECK: "driver_red",    # retry RED — must be distinct from the
                                                 # accept value above (both used to be
                                                 # "driver", which made accept and reject
                                                 # indistinguishable to the graph's routing)
    CheckpointType.GREEN_CHECK: "driver",      # retry GREEN
    CheckpointType.REFACTOR_CHECK: "refactorer",  # retry REFACTOR
}


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
    default — Reviewer never takes Driver's word for pass/fail.

    Uses sys.executable (the interpreter running Battalion) rather than the
    bare "python" from PATH, so the clean-tree re-run uses the same Python
    that has Battalion's test dependencies installed regardless of which
    venv the caller activated."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
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
    checkpoint: CheckpointType,
    call_llm_fn: Callable = call_llm,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    make_clean_copy_fn: Callable[[Path], Path] = make_clean_copy,
    run_tests_fn: Callable[[Path], TestRunResult] = run_tests_via_subprocess,
) -> RunState:
    """Run the Reviewer node: independently re-run tests from a clean copy
    of the current src/ tree, and return updated state.

    checkpoint (required, BTN-12/ADR-007) determines both which outcome
    counts as accept (RED_CHECK expects failure; GREEN_CHECK and
    REFACTOR_CHECK expect success) and where the ticket goes next on
    accept/reject (_NEXT_PHASE_ON_ACCEPT / _RETRY_PHASE_ON_REJECT above).
    There's no default — silently defaulting would re-hide the RED-check
    polarity bug this parameter exists to fix.

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

    expect_pass = _EXPECT_PASS_BY_CHECKPOINT[checkpoint]
    accepted = result.passed == expect_pass

    if accepted:
        next_phase = _NEXT_PHASE_ON_ACCEPT[checkpoint]
        next_status = RunStatus.DONE if next_phase == "done" else RunStatus.IN_PROGRESS
        return state.model_copy(update={"phase": next_phase, "status": next_status})

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

    # Per-checkpoint-type counter (ADR-009): a RED-check rejection and a
    # GREEN-check rejection don't share a cycle count.
    same_checkpoint_count = sum(
        1 for r in state.reviewer_rejection_history if r.checkpoint == checkpoint
    )
    cycle_number = same_checkpoint_count + 1
    new_history = state.reviewer_rejection_history + [
        RejectionRecord(cause=cause.strip(), cycle_number=cycle_number, checkpoint=checkpoint)
    ]
    return state.model_copy(update={
        "phase": _RETRY_PHASE_ON_REJECT[checkpoint],
        "status": RunStatus.IN_PROGRESS,
        "reviewer_rejection_history": new_history,
    })
