"""Independent Reviewer checkpoint policy (BTN-6, BTN-12, BTN-164).

RED accepts only a collected-test failure; GREEN and REFACTOR accept only a
valid pass. Harness outcomes pause through the typed infrastructure path.
Reviewer has no project write tools; test mechanics own disposable scratch.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from battalion.execution import (
    record_review_decision, record_reviewer_workspace, record_test_execution,
)
from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.nodes.errors import RoleOutputError, WriteScopeMisconfigured
from battalion.prompts.loader import load_system_prompt
from battalion.reviewer_testing import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    ReviewerTestExecutionError,
    TestRunResult,
    make_clean_copy,
    run_tests_via_subprocess,
)
from battalion.scope.tool_binding import build_write_tools
from battalion.state.models import (
    CheckpointType, RejectionRecord, RunState, RunStatus, TestExecutionClassification,
)

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


class EmptyReviewContent(RoleOutputError):
    """Raised when the LLM returns empty/whitespace-only rejection-cause
    content. Without this check, an unusable cause string could be
    recorded, undermining same-cause-twice detection (interrupt trigger #1,
    built in BTN-8)."""


class SourceTreeMissing(RoleOutputError):
    """Backward-compatible name for a missing configured project root."""


def run_reviewer(
    state: RunState,
    base_dir: str | Path,
    llm_config: NodeLLMConfig,
    checkpoint: CheckpointType,
    call_llm_fn: Callable = call_llm,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    make_clean_copy_fn: Callable[[Path], Path] = make_clean_copy,
    run_tests_fn: Callable[[Path], TestRunResult] | None = None,
    on_stream: Callable[[dict], None] | None = None,
    instinct_context: str | None = None,
    test_timeout_seconds: float = DEFAULT_TEST_TIMEOUT_SECONDS,
    cancellation_requested: Callable[[], bool] | None = None,
) -> RunState:
    """Independently re-run tests from a clean copy of the project root.

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

    project_root = Path(base_dir)
    if not project_root.is_dir():
        raise SourceTreeMissing(
            f"Configured project root does not exist at {project_root} — "
            "Reviewer has nothing to test."
        )

    clean_dir = make_clean_copy_fn(project_root)
    try:
        record_reviewer_workspace(clean_dir)
        if run_tests_fn is None:
            result = run_tests_via_subprocess(
                clean_dir,
                timeout_seconds=test_timeout_seconds,
                cancellation_requested=cancellation_requested,
            )
        else:
            result = run_tests_fn(clean_dir)
    finally:
        # Clean-tree copies are single-use scratch space — remove them so
        # repeated review runs don't leak directories under /tmp.
        shutil.rmtree(clean_dir, ignore_errors=True)

    record_test_execution(result.to_evidence())
    valid_verdicts = {
        TestExecutionClassification.PASSED,
        TestExecutionClassification.TEST_FAILED,
    }
    if result.classification not in valid_verdicts:
        raise ReviewerTestExecutionError(result)

    expect_pass = _EXPECT_PASS_BY_CHECKPOINT[checkpoint]
    accepted = (
        result.classification is TestExecutionClassification.PASSED
        if expect_pass
        else result.classification is TestExecutionClassification.TEST_FAILED
    )

    if accepted:
        record_review_decision(checkpoint, accepted=True)
        next_phase = _NEXT_PHASE_ON_ACCEPT[checkpoint]
        next_status = RunStatus.DONE if next_phase == "done" else RunStatus.IN_PROGRESS
        return state.model_copy(update={"phase": next_phase, "status": next_status})

    resolved_prompt = system_prompt or load_system_prompt(
        "reviewer", prompts_dir=prompts_dir
    )
    user_content = f"Test output:\n{result.output}"
    if instinct_context:
        user_content = f"{instinct_context}\n\n{user_content}"
    messages = [
        {"role": "system", "content": resolved_prompt},
        {"role": "user", "content": user_content},
    ]
    if on_stream is not None:
        response = call_llm_fn("reviewer", llm_config, messages, on_stream=on_stream)
    else:
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
    record_review_decision(checkpoint, accepted=False, cause=cause.strip())
    return state.model_copy(update={
        "phase": _RETRY_PHASE_ON_REJECT[checkpoint],
        "status": RunStatus.IN_PROGRESS,
        "reviewer_rejection_history": new_history,
    })
