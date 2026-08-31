"""Transport- and IO-free recovery classification from durable run evidence."""
from dataclasses import dataclass
from typing import Literal

from battalion.state.models import ProgressStage, RunState, RunStatus


class UnsafeRecoveryError(Exception):
    """The graph cannot continue with the supplied recovery evidence/configuration."""


@dataclass(frozen=True)
class RecoveryAssessment:
    disposition: Literal["recoverable", "terminal"]
    stage: ProgressStage | None
    message: str


def assess_recovery(state: RunState) -> RecoveryAssessment | None:
    progress = state.graph_progress
    if progress is not None and progress.stage is ProgressStage.ATTEMPT_STARTED:
        return RecoveryAssessment(
            "terminal", progress.stage,
            f"Attempt {progress.execution_id} started without a durable outcome. "
            "Replay is unsafe because provider calls or workspace writes may have occurred. "
            "Inspect the execution record and workspace, then start a new run from the "
            "reviewed workspace; do not edit the saved state to force replay.",
        )
    if state.resume_intent is not None and not state.resume_intent.completed:
        return RecoveryAssessment(
            "recoverable", progress.stage if progress else ProgressStage.BEFORE_ATTEMPT,
            "Resume can replay the saved authorization without recording another decision.",
        )
    if progress is not None and (
        state.status in {RunStatus.IN_PROGRESS, RunStatus.NOT_STARTED}
        or state.phase == "recursion_limit_exceeded"
    ):
        return RecoveryAssessment(
            "recoverable", progress.stage,
            f"Resume can continue from the durable checkpoint at {progress.next_node}.",
        )
    return None
