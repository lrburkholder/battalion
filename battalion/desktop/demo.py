"""Deterministic, credential-free production UI showcase data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from battalion.application import (
    IntelInspection,
    ProjectInspection,
    ProjectRunInspection,
    RunInspection,
)
from battalion.identity import ProjectIdentity, RunCatalogEntry
from battalion.intel.models import AcceptedInstinct, CandidateInstinct
from battalion.state.models import (
    Budget,
    CheckpointType,
    ExecutionRecord,
    NodeExecution,
    OperatorSummary,
    ReviewResult,
    RunState,
    RunStatus,
    TestOutcome,
)


DEMO_PROJECT_ID = UUID("57000000-0000-4000-8000-000000000057")
PAUSED_RUN_ID = "57000000-0000-4000-8000-000000000001"
DONE_RUN_ID = "57000000-0000-4000-8000-000000000002"
CAPTURED_AT = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)


def showcase_snapshot() -> tuple[ProjectInspection, IntelInspection]:
    """Return a stable snapshot containing only fictional public-safe evidence."""

    paused = _run(
        run_id=PAUSED_RUN_ID,
        alias="BTN-57-showcase",
        ticket_id="BTN-57",
        status=RunStatus.AWAITING_HUMAN,
        phase="driver_green",
        execution=_execution(
            execution_id="showcase-driver-red-1",
            role="driver",
            phase="driver_red",
            outcome="succeeded",
            what_happened="Added deterministic failing coverage for the Pages allowlist.",
            next_step="Review the evidence, then authorize the GREEN attempt.",
        ),
    )
    completed = _run(
        run_id=DONE_RUN_ID,
        alias="BTN-43-human-actions",
        ticket_id="BTN-43",
        status=RunStatus.DONE,
        phase="done",
        execution=_execution(
            execution_id="showcase-reviewer-3",
            role="reviewer",
            phase="reviewer_refactor",
            outcome="succeeded",
            what_happened="Verified the human-action boundary and durable audit trail.",
            next_step="The run is complete; review its retained evidence.",
        ),
    )
    project = ProjectInspection(
        project_root=Path("battalion-showcase"),
        identity=ProjectIdentity(project_id=DEMO_PROJECT_ID, created_at=CAPTURED_AT),
        runs=(paused, completed),
    )
    return project, IntelInspection(
        accepted=(_instinct(AcceptedInstinct, "INS-SCOPED-WRITES", "accepted"),),
        candidates=(_instinct(CandidateInstinct, "INS-REVIEW-EVIDENCE", "candidate"),),
    )


def _run(
    *,
    run_id: str,
    alias: str,
    ticket_id: str,
    status: RunStatus,
    phase: str,
    execution: NodeExecution,
) -> ProjectRunInspection:
    state = RunState(
        schema_version="1.0",
        run_id=run_id,
        run_alias=alias,
        project_id=str(DEMO_PROJECT_ID),
        ticket_id=ticket_id,
        spec="Deterministic showcase data; no provider or repository access.",
        status=status,
        phase=phase,
        retry_bound=2,
        budget=Budget(limit=12, used=4),
        execution_record=ExecutionRecord(node_executions=[execution]),
    )
    entry = RunCatalogEntry(
        run_id=run_id,
        display_alias=alias,
        ticket_id=ticket_id,
        state_path=f".battalion/state/{run_id}.json",
    )
    return ProjectRunInspection(
        catalog_entry=entry,
        availability="available",
        inspection=RunInspection(
            run_id=run_id,
            run_alias=alias,
            state_version=state.schema_version,
            state_path=Path(entry.state_path),
            state=state,
            costs={},
        ),
    )


def _execution(
    *,
    execution_id: str,
    role: str,
    phase: str,
    outcome: str,
    what_happened: str,
    next_step: str,
) -> NodeExecution:
    return NodeExecution(
        execution_id=execution_id,
        role=role,
        phase=phase,
        model_identity="demo/local-model",
        started_at=CAPTURED_AT,
        ended_at=CAPTURED_AT,
        outcome=outcome,
        test_outcome=TestOutcome(
            checkpoint=CheckpointType.GREEN_CHECK,
            passed=True,
            expected_to_pass=True,
            accepted=True,
        ),
        review_result=ReviewResult(
            checkpoint=CheckpointType.GREEN_CHECK,
            verdict="accepted",
        ),
        operator_summary=OperatorSummary(
            what_i_did=what_happened,
            what_should_happen_next=next_step,
            verification_performed=["Offline focused tests passed."],
            artifact_paths=["docs/index.md"],
            last_role=role,
            last_node=phase,
            last_phase=phase,
        ),
    )


def _instinct(model, instinct_id: str, lifecycle: str):
    data = {
        "schema_version": "1.0",
        "instinct_id": instinct_id,
        "lifecycle": lifecycle,
        "recommendation": (
            "Keep node writes inside the approved phase-specific scope."
            if lifecycle == "accepted"
            else "Link every review conclusion to durable execution evidence."
        ),
        "evidence": [{
            "run_id": DONE_RUN_ID,
            "node_execution_id": "showcase-reviewer-3",
            "reference": "execution_record.node_executions[0]",
            "description": "Deterministic showcase evidence for the production UI.",
        }],
        "audience": ["driver", "reviewer"],
        "applicability": {
            "description": "Repository work coordinated through Battalion",
            "include": ["battalion/**"],
        },
        "tags": ["scope", "evidence"],
        "creation_provenance": {
            "originating_run_id": DONE_RUN_ID,
            "originating_node_execution_ids": ["showcase-reviewer-3"],
            "created_at": CAPTURED_AT.isoformat(),
            "created_by": "recon",
        },
    }
    if lifecycle == "accepted":
        data["acceptance_provenance"] = {
            "accepted_at": CAPTURED_AT.isoformat(),
            "accepted_by": "showcase-operator",
        }
    return model.model_validate(data)
