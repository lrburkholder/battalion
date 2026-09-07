"""Connect fresh source observation to verified execution history."""

from __future__ import annotations

from battalion.project_source import ProjectSourceChanged, ProjectSourceSnapshot, ProjectSourceUnavailable, revalidate_project_source
from battalion.state.models import ProgressStage, RunState


def verified_run_write_digests(state: RunState) -> dict[str, str]:
    """Last completed scoped write per path; declarations and snapshots do not count."""
    digests: dict[str, str] = {}
    for attempt in state.execution_record.node_executions:
        completed = attempt.outcome == "succeeded" or (
            attempt.outcome == "interrupted" and bool(attempt.interrupt_ids)
            and all(
                0 <= index < len(state.interrupt_log)
                and state.interrupt_log[index].node_execution_id == attempt.execution_id
                and state.interrupt_log[index].trigger in {"manual-checkpoint", "budget-exceeded"}
                for index in attempt.interrupt_ids
            )
        )
        for write in attempt.verified_scoped_writes:
            if completed and write.originating_run_id == state.run_id:
                digests[write.path] = write.sha256
            else:
                # A later unsuccessful write cannot inherit an earlier receipt.
                digests.pop(write.path, None)
    return digests


def current_project_source_revision(state: RunState, observed: ProjectSourceSnapshot) -> str:
    baseline = state.project_source_snapshot
    if baseline is None:
        raise ProjectSourceUnavailable("Run has no pre-execution source snapshot; start a new admitted Run.")
    unfinished = [item for item in state.execution_record.node_executions if item.outcome == "in-progress"]
    progress = state.graph_progress
    unstarted = (len(unfinished) == 1 and progress is not None
                 and progress.stage is ProgressStage.ATTEMPT_CREATED
                 and progress.execution_id == unfinished[0].execution_id
                 and progress.next_node == unfinished[0].phase)
    if unfinished and not unstarted:
        raise ProjectSourceChanged("An unfinished attempt prevents source revalidation.")
    return revalidate_project_source(
        baseline, observed, verified_write_digests=verified_run_write_digests(state),
    )
