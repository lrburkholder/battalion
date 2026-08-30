"""Pure read-only projections for the desktop operator console.

Keeping formatting free of Qt makes missing-data semantics deterministic and
lets the presentation boundary be tested without a display server.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from battalion.actors import format_actor_attribution
from battalion.application import IntelInspection, ProjectRunInspection, RunInspection
from battalion.intel.models import AcceptedInstinct, CandidateInstinct
from battalion.state.models import LLMCallCost, NodeExecution, RunStatus
from battalion.workers import WorkerRecord


ACTIVE_STATUSES = {
    RunStatus.NOT_STARTED,
    RunStatus.IN_PROGRESS,
    RunStatus.AWAITING_HUMAN,
    RunStatus.BLOCKED,
}


def partition_runs(
    runs: Iterable[ProjectRunInspection],
) -> tuple[tuple[ProjectRunInspection, ...], tuple[ProjectRunInspection, ...]]:
    """Split Work from History without hiding unreadable durable entries."""
    work: list[ProjectRunInspection] = []
    history: list[ProjectRunInspection] = []
    for run in runs:
        if (
            run.inspection is not None
            and run.inspection.state.status in ACTIVE_STATUSES
        ):
            work.append(run)
        else:
            history.append(run)
    return tuple(work), tuple(history)


def run_label(run: ProjectRunInspection) -> str:
    entry = run.catalog_entry
    if run.inspection is None:
        return f"{entry.ticket_id} · {entry.display_alias} · {run.availability}"
    state = run.inspection.state
    legacy = " · legacy identifier" if entry.legacy_id else ""
    return f"{state.ticket_id} · {entry.display_alias} · {state.status.value}{legacy}"


def render_run(run: ProjectRunInspection, worker: WorkerRecord | None = None) -> str:
    """Render a complete run summary with explicit missing evidence."""
    entry = run.catalog_entry
    if run.inspection is None:
        return "\n".join((
            f"Run: {entry.display_alias}",
            f"Ticket: {entry.ticket_id}",
            f"Availability: {run.availability}",
            f"Limitation: {run.limitation or 'Unavailable'}",
            f"Legacy identifier: {'yes' if entry.legacy_id else 'no'}",
        ))

    inspection = run.inspection
    state = inspection.state
    recovery = None if worker is not None and worker.active else inspection.recovery
    executions = state.execution_record.node_executions
    calls = [call for execution in executions for call in execution.llm_calls]
    lines = [
        f"Run: {inspection.run_alias or inspection.run_id}",
        f"Canonical ID: {inspection.run_id}",
        f"Ticket: {state.ticket_id}",
        f"Status: {state.status.value}",
        f"Phase: {state.phase}",
        f"State contract: {inspection.state_version}",
        f"Legacy identifier: {'yes' if entry.legacy_id else 'no'}",
        f"Node attempts: {len(executions)}",
        f"Tokens: {sum(call.input_tokens for call in calls)} input / "
        f"{sum(call.output_tokens for call in calls)} output",
        f"Cost: {_aggregate_cost(calls)}",
        f"Worker: {_worker_summary(worker, allow_replay=recovery is None or recovery.disposition == 'recoverable')}",
    ]
    if recovery is not None:
        lines.append(f"Recovery: {recovery.disposition} · {recovery.stage.value if recovery.stage else 'unavailable'}")
        lines.append(recovery.message)
    elif state.status is RunStatus.AWAITING_HUMAN:
        lines.append("Human action: interrupt resolution and resume available")
    queued_interventions = sum(
        item.disposition.value == "queued" for item in state.interventions
    )
    lines.append(f"Queued interventions: {queued_interventions}")
    if state.human_action_log:
        lines.extend(("", "HUMAN ACTIONS"))
        lines.extend(
            f"- {item.occurred_at.isoformat()} · "
            f"{format_actor_attribution(item.actor, item.actor_id)} · {item.kind} · "
            f"{item.target} · {item.disposition} · resulting "
            f"{item.resulting_status.value}/{item.resulting_phase}"
            for item in state.human_action_log
        )
    return "\n".join(lines)


def render_execution(execution: NodeExecution) -> str:
    """Render every evidence category required by BTN-42."""
    lines = [
        "EXECUTION",
        f"ID: {execution.execution_id}",
        f"Role: {execution.role}",
        f"Phase: {execution.phase}",
        f"Model: {execution.model_identity}",
        f"Outcome: {execution.outcome}",
        f"Attempt disposition: {execution.attempt_disposition or 'Unavailable (legacy)'}",
        f"Started: {execution.started_at.isoformat()}",
        f"Ended: {execution.ended_at.isoformat() if execution.ended_at else 'Not completed'}",
        "",
        "PROMPT PROVENANCE",
    ]
    prompt = execution.prompt_provenance
    if prompt is None:
        lines.append("Unavailable (legacy or uncaptured execution evidence)")
    else:
        lines.extend((
            f"Identity: {prompt.template_identity}",
            f"Path: {prompt.template_path}",
            f"Contract version: {prompt.contract_version}",
            f"Template hash ({prompt.hash_algorithm}): {prompt.template_hash}",
            f"Battalion revision: {prompt.battalion_revision or 'Unavailable'}",
            f"Model configuration: {prompt.model_configuration_identity}",
        ))

    lines.extend(("", "GIT PROVENANCE"))
    code = execution.code_provenance
    if code is None:
        lines.append("Unavailable (legacy or uncaptured execution evidence)")
    elif not code.repository_available:
        lines.append("Repository unavailable when this attempt was captured")
    else:
        lines.extend((
            f"Base revision ({code.object_id_algorithm}): {code.base_commit_object_id}",
            f"Branch: {code.branch or 'detached HEAD'}",
            f"Detached: {_yes_no(code.detached)}",
            f"Dirty at start: {_yes_no(code.dirty_at_start)}",
            f"Dirty at end: {_yes_no(code.dirty_at_end)}",
            "Exact workspace reconstructable: "
            f"{_yes_no(code.exact_workspace_reconstructable)}",
            f"Limitation: {code.reconstruction_limitation or 'None recorded'}",
        ))

    lines.extend(("", "BOUNDED CONTEXT"))
    if not execution.input_references:
        lines.append("Unavailable")
    for reference in execution.input_references:
        lines.extend((
            f"- {reference.kind}: {reference.reference}",
            f"  Inclusion: {reference.inclusion_reason or 'Unavailable'}",
            f"  Digest: {reference.sha256 or 'Unavailable'}",
            f"  Truncated: {'yes' if reference.truncated else 'no'}",
            "  Bytes: " + (
                f"{reference.hashed_bytes}/{reference.observed_bytes} hashed"
                if reference.observed_bytes is not None
                else "Unavailable"
            ),
        ))

    lines.extend(("", "ROLE-CONTRACT CORRECTION"))
    violation = execution.role_contract_violation
    if violation is None:
        lines.append("None recorded")
    else:
        lines.extend((
            f"Reason: {violation.reason_code}",
            f"Detail: {violation.detail}",
            f"Correction attempt: {violation.attempt_number}",
            f"Mutation applied: {_yes_no(violation.mutation_applied)}",
            f"Disposition: {violation.resulting_disposition}",
            "Offending paths: " + (", ".join(violation.offending_paths) or "None recorded"),
        ))

    lines.extend(("", "ARTIFACTS"))
    if not execution.artifact_provenance:
        lines.append("None recorded")
    for artifact in execution.artifact_provenance:
        lines.append(
            f"- {artifact.path} · sha256 {artifact.sha256} · "
            f"origin {artifact.originating_run_id}/{artifact.originating_node_execution_id}"
        )

    lines.extend(("", "VERIFICATION"))
    if execution.test_outcome is None:
        lines.append("Tests: Unavailable")
    else:
        test = execution.test_outcome
        lines.append(
            f"Tests: {test.checkpoint.value} · passed={_yes_no(test.passed)} · "
            f"expected-to-pass={_yes_no(test.expected_to_pass)} · "
            f"accepted={_yes_no(test.accepted)}"
        )
    if execution.review_result is None:
        lines.append("Review: Unavailable")
    else:
        review = execution.review_result
        lines.append(
            f"Review: {review.checkpoint.value} · {review.verdict} · "
            f"{review.cause or 'no rejection cause'}"
        )

    process = execution.test_execution
    if process is not None:
        lines.extend((
            f"Pytest classification: {process.classification.value}",
            f"Command: {process.command!r}",
            f"Working directory: {process.working_directory}",
            f"Exit code: {process.returncode}",
            f"Collected: {process.tests_collected}; failures: {process.failures}; errors: {process.errors}",
            f"Duration: {process.duration_ms} ms; timeout: {process.timeout_seconds:g} s",
            f"Timed out: {_yes_no(process.timed_out)}; cancelled: {_yes_no(process.cancelled)}",
            f"Cleanup attempted: {_yes_no(process.cleanup_attempted)}; succeeded: {_yes_no(process.cleanup_succeeded)}",
            f"Detail: {process.detail or 'None'}",
            f"STDOUT ({process.stdout_observed_bytes} bytes; truncated={_yes_no(process.stdout_truncated)}):",
            process.stdout,
            f"STDERR ({process.stderr_observed_bytes} bytes; truncated={_yes_no(process.stderr_truncated)}):",
            process.stderr,
        ))
    elif execution.role == "reviewer":
        lines.append("Pytest process evidence: Unavailable (legacy or uncaptured)")

    lines.extend(("", "TOKEN AND COST EVIDENCE"))
    if not execution.llm_calls:
        lines.append("No model-call usage recorded")
    for call in execution.llm_calls:
        lines.append(_render_call(call))

    lines.extend(("", "OPERATOR SUMMARY"))
    summary = execution.operator_summary
    if summary is None:
        lines.append("Unavailable")
    else:
        lines.extend((
            f"What happened: {summary.what_i_did}",
            f"Next: {summary.what_should_happen_next}",
            "Open questions: " + ("; ".join(summary.open_questions) or "None"),
            "Verification: "
            + ("; ".join(summary.verification_performed) or "None recorded"),
        ))
    return "\n".join(lines)


def render_intel_item(item: AcceptedInstinct | CandidateInstinct) -> str:
    lifecycle = item.lifecycle.value if hasattr(item.lifecycle, "value") else item.lifecycle
    lines = [
        f"Instinct: {item.instinct_id}",
        f"Lifecycle: {lifecycle}",
        f"Recommendation: {item.recommendation}",
        f"Audience: {', '.join(role.value for role in item.audience)}",
        f"Applicability: {item.applicability.description}",
        f"Tags: {', '.join(item.tags)}",
        f"Originating run: {item.creation_provenance.originating_run_id}",
        "Evidence:",
    ]
    lines.extend(
        f"- {evidence.run_id}/{evidence.node_execution_id} · "
        f"{evidence.reference} · {evidence.description}"
        for evidence in item.evidence
    )
    if isinstance(item, CandidateInstinct):
        lines.append(
            "Authority: candidate only; a human may promote or reject it through "
            "the canonical review workflow"
        )
    else:
        lines.append(
            "Accepted by: "
            f"{format_actor_attribution(item.acceptance_provenance.accepted_by, item.acceptance_provenance.accepted_by_actor_id)} at "
            f"{item.acceptance_provenance.accepted_at.isoformat()}"
        )
    return "\n".join(lines)


def intel_empty(inspection: IntelInspection) -> bool:
    return not inspection.accepted and not inspection.candidates


def _render_call(call: LLMCallCost) -> str:
    if call.cost is None:
        cost = "unknown · source=unknown"
    else:
        cost = f"{call.cost} {call.cost_currency} · source={call.cost_source.value}"
    return (
        f"- {call.call_id} · model={call.model} · input={call.input_tokens} · "
        f"output={call.output_tokens} · cost={cost}"
    )


def _aggregate_cost(calls: Iterable[LLMCallCost]) -> str:
    calls = tuple(calls)
    if not calls:
        return "Unavailable (no model-call usage recorded)"
    known: dict[str, Decimal] = {}
    unknown = 0
    sources: set[str] = set()
    for call in calls:
        if call.cost is None:
            unknown += 1
            continue
        known[call.cost_currency] = known.get(call.cost_currency, Decimal(0)) + call.cost
        sources.add(call.cost_source.value)
    totals = ", ".join(f"{amount} {currency}" for currency, amount in sorted(known.items()))
    if not totals:
        totals = "no known monetary amount"
    return f"{totals}; {unknown} unknown call(s); sources={','.join(sorted(sources)) or 'unknown'}"


def _worker_summary(worker: WorkerRecord | None, *, allow_replay: bool = True) -> str:
    if worker is None:
        return "Unavailable (no worker record)"
    recovery = " · recoverable from durable state" if worker.recoverable and allow_replay else ""
    error = f" · {worker.error}" if worker.error else ""
    return f"{worker.status.value}{recovery}{error}"


def _yes_no(value: bool | None) -> str:
    if value is None:
        return "Unavailable"
    return "yes" if value else "no"
