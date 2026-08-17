"""Deterministic, framework-neutral data and scenario for desktop spikes.

This module is test infrastructure.  Operator actions are simulations of the
accepted product direction, not additional Battalion application operations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from battalion.intel.models import CandidateInstinct
from battalion.observation import ObservationEvent
from battalion.state.models import (
    ArtifactProvenance,
    Budget,
    CodeProvenance,
    CostSource,
    EvidenceReference,
    ExecutionRecord,
    InterruptLogEntry,
    LLMCallCost,
    NodeExecution,
    OperatorSummary,
    PromptProvenance,
    RunState,
    RunStatus,
)


FIXED_NOW = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
RUN_ACTIVE = "10000000-0000-4000-8000-000000000037"
RUN_COMPLETE = "20000000-0000-4000-8000-000000000037"
PROJECT_ID = "30000000-0000-4000-8000-000000000037"
SECOND_PROJECT_ID = "30000000-0000-4000-8000-000000000038"
STREAM_ID = UUID("40000000-0000-4000-8000-000000000037")
ATTEMPT_ID = UUID("50000000-0000-4000-8000-000000000037")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
REVISION = "d" * 40


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FakeProject(_FrozenModel):
    project_id: str
    name: str
    path_hint: str
    tickets: list[dict[str, str]]


class SimulatedAction(_FrozenModel):
    action_id: str
    kind: Literal[
        "resolve-interrupt",
        "review-candidate",
        "correction",
        "design-decision",
    ]
    target: str
    actor: str
    payload: dict[str, Any]


class ScenarioStep(_FrozenModel):
    step_id: str
    surface: Literal["work", "history", "execution", "live", "intel", "action"]
    instruction: str
    expected: dict[str, Any]


class MeasurementCategory(_FrozenModel):
    category: Literal[
        "packaging",
        "process",
        "resource",
        "accessibility",
        "testability",
        "failure-recovery",
        "permission-surface",
        "learning",
        "implementation-complexity",
    ]
    observations: list[str]
    method: str
    unit: str | None = None


class BenchmarkBundle(_FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    fixture_id: Literal["BTN-37-desktop-v1"] = "BTN-37-desktop-v1"
    generated_at: datetime
    provider_mode: Literal["disabled"] = "disabled"
    projects: list[FakeProject]
    runs: list[RunState]
    candidates: list[CandidateInstinct]
    observations: list[ObservationEvent]
    actions: list[SimulatedAction]
    scenario: list[ScenarioStep]
    measurements: list[MeasurementCategory]

    @model_validator(mode="after")
    def validate_control_case(self) -> "BenchmarkBundle":
        if len({step.step_id for step in self.scenario}) != len(self.scenario):
            raise ValueError("scenario step identifiers must be unique")
        categories = {item.category for item in self.measurements}
        if len(categories) != 9:
            raise ValueError("all nine benchmark measurement categories are required")
        return self


class TraceEntry(_FrozenModel):
    step_id: str
    observed: dict[str, Any]


class BenchmarkTrace(_FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    fixture_id: Literal["BTN-37-desktop-v1"] = "BTN-37-desktop-v1"
    framework: Literal["tauri", "pyside6", "electron"]
    entries: list[TraceEntry]


def _execution(
    *, execution_id: str, role: str, phase: str, model: str, cost: Decimal | None
) -> NodeExecution:
    cost_source = CostSource.UNKNOWN if cost is None else CostSource.PROVIDER_REPORTED
    artifact = ArtifactProvenance(
        path="src/widget.py",
        sha256=SHA_C,
        originating_run_id=RUN_COMPLETE,
        originating_node_execution_id=execution_id,
    )
    return NodeExecution(
        execution_id=execution_id,
        role=role,
        phase=phase,
        model_identity=model,
        input_references=[EvidenceReference(
            kind="state",
            reference="RunState.spec",
            sha256=SHA_A,
            hash_algorithm="sha256",
            inclusion_reason="ticket specification",
            truncated=False,
            observed_bytes=128,
            hashed_bytes=128,
        )],
        output_reference=f"execution_record.node_executions[{0 if role == 'architect' else 1}]",
        started_at=FIXED_NOW,
        ended_at=FIXED_NOW.replace(minute=FIXED_NOW.minute + 1),
        outcome="succeeded",
        artifact_provenance=[] if role == "architect" else [artifact],
        llm_calls=[LLMCallCost(
            call_id=f"call-{execution_id}",
            model=model,
            input_tokens=120,
            output_tokens=80,
            cost=cost,
            cost_currency=None if cost is None else "USD",
            cost_source=cost_source,
        )],
        operator_summary=OperatorSummary(
            what_i_did=f"Completed the {phase} benchmark attempt.",
            what_should_happen_next="Continue through the recorded workflow.",
            verification_performed=["Validated deterministic fixture output."],
            artifact_paths=[] if role == "architect" else ["src/widget.py"],
            last_role=role,
            last_node=phase,
            last_phase=phase,
        ),
        prompt_provenance=PromptProvenance(
            template_identity=f"{role}/v1",
            template_path=f"prompts/{role}.md",
            contract_version=f"{role}/v1",
            template_hash=SHA_B,
            battalion_revision=REVISION,
            model_configuration_identity=SHA_C,
        ),
        code_provenance=CodeProvenance(
            repository_available=True,
            base_commit_object_id=REVISION,
            object_id_algorithm="sha1",
            branch="codex/fixture-example",
            detached=False,
            dirty_at_start=False,
            dirty_at_end=False,
            exact_workspace_reconstructable=True,
        ),
    )


def build_bundle() -> BenchmarkBundle:
    """Build the exact control case consumed by every framework spike."""
    completed = RunState(
        schema_version="1.0",
        run_id=RUN_COMPLETE,
        run_alias="BTN-101-complete",
        project_id=PROJECT_ID,
        ticket_id="BTN-101",
        spec="Add deterministic widget behavior.",
        status=RunStatus.DONE,
        phase="done",
        retry_bound=2,
        budget=Budget(limit=100, used=18),
        execution_record=ExecutionRecord(node_executions=[
            _execution(
                execution_id="node-architect-1",
                role="architect",
                phase="architect",
                model="fixture/architect",
                cost=Decimal("0.0123"),
            ),
            _execution(
                execution_id="node-driver-green-1",
                role="driver",
                phase="driver_green",
                model="fixture/driver",
                cost=None,
            ),
        ]),
    )
    active = RunState(
        schema_version="1.0",
        run_id=RUN_ACTIVE,
        run_alias="BTN-102-interrupted",
        project_id=PROJECT_ID,
        ticket_id="BTN-102",
        spec="Exercise an operator checkpoint.",
        status=RunStatus.AWAITING_HUMAN,
        phase="driver_green",
        retry_bound=2,
        budget=Budget(limit=100, used=9),
        resume_target="driver_green",
        interrupt_log=[InterruptLogEntry(
            trigger="manual-checkpoint",
            timestamp=FIXED_NOW,
            context={"checkpoint": "driver_green"},
        )],
    )
    candidate = CandidateInstinct.model_validate({
        "instinct_id": "INS-BENCHMARK-SCOPE",
        "lifecycle": "candidate",
        "recommendation": "Keep desktop presentation outside state authority.",
        "evidence": [{
            "run_id": RUN_COMPLETE,
            "node_execution_id": "node-driver-green-1",
            "reference": "execution_record.node_executions[1]",
            "description": "The bounded fixture preserves the application boundary.",
        }],
        "audience": ["architect", "driver"],
        "applicability": {
            "description": "Desktop presentation adapters.",
            "include": ["framework spikes"],
            "exclude": ["graph policy"],
        },
        "tags": ["desktop", "authority"],
        "creation_provenance": {
            "originating_run_id": RUN_COMPLETE,
            "originating_node_execution_ids": ["node-driver-green-1"],
            "created_at": FIXED_NOW,
            "created_by": "recon",
        },
    })
    observations = [
        ObservationEvent.model_validate({
            "event_id": uuid5(STREAM_ID, "1"),
            "run_id": RUN_ACTIVE,
            "stream_id": STREAM_ID,
            "sequence": 1,
            "occurred_at": FIXED_NOW,
            "category": "transient",
            "kind": "node-started",
            "node": "driver_green",
            "attempt_id": ATTEMPT_ID,
            "payload": {"type": "node_start", "node": "driver_green"},
        }),
        ObservationEvent.model_validate({
            "event_id": uuid5(STREAM_ID, "2"),
            "run_id": RUN_ACTIVE,
            "stream_id": STREAM_ID,
            "sequence": 2,
            "occurred_at": FIXED_NOW,
            "category": "action-required",
            "kind": "interrupt",
            "node": "driver_green",
            "attempt_id": ATTEMPT_ID,
            "payload": {"type": "interrupt", "trigger": "manual-checkpoint"},
        }),
        ObservationEvent.model_validate({
            "event_id": uuid5(STREAM_ID, "3"),
            "run_id": RUN_ACTIVE,
            "stream_id": STREAM_ID,
            "sequence": 3,
            "occurred_at": FIXED_NOW,
            "category": "durable",
            "kind": "state-checkpoint",
            "payload": {
                "state_version": "1.0",
                "status": "awaiting-human",
                "phase": "driver_green",
            },
        }),
    ]
    actions = [
        SimulatedAction(
            action_id="resolve-interrupt",
            kind="resolve-interrupt",
            target=RUN_ACTIVE,
            actor="benchmark-operator",
            payload={"resolution": "continue", "expected_status": "in-progress"},
        ),
        SimulatedAction(
            action_id="review-candidate",
            kind="review-candidate",
            target=candidate.instinct_id,
            actor="benchmark-operator",
            payload={"decision": "accept", "expected_disposition": "promoted"},
        ),
        SimulatedAction(
            action_id="queue-correction",
            kind="correction",
            target="driver_green",
            actor="benchmark-operator",
            payload={"content": "Preserve the public function signature.", "timing": "next-attempt"},
        ),
        SimulatedAction(
            action_id="queue-design-decision",
            kind="design-decision",
            target="architect",
            actor="benchmark-operator",
            payload={"content": "Keep persistence project-local.", "timing": "next-attempt"},
        ),
    ]
    scenario = [
        ScenarioStep(step_id="work", surface="work", instruction="Show project tickets and active work.", expected={"project_id": PROJECT_ID, "ticket_id": "BTN-102"}),
        ScenarioStep(step_id="history", surface="history", instruction="Show completed and interrupted run history.", expected={"run_ids": [RUN_COMPLETE, RUN_ACTIVE]}),
        ScenarioStep(step_id="execution", surface="execution", instruction="Inspect node attempt, artifact, and summary evidence.", expected={"execution_id": "node-driver-green-1", "artifact": "src/widget.py"}),
        ScenarioStep(step_id="cost", surface="execution", instruction="Display known and unknown costs without hiding token usage.", expected={"known_cost": "0.0123", "currency": "USD", "unknown_cost_calls": 1}),
        ScenarioStep(step_id="provenance", surface="execution", instruction="Display prompt, model, revision, context, and code provenance.", expected={"revision": REVISION, "context_hash": SHA_A, "exact_workspace_reconstructable": True}),
        ScenarioStep(step_id="live", surface="live", instruction="Render transient, action-required, and durable simulated transitions.", expected={"sequences": [1, 2, 3], "kinds": ["node-started", "interrupt", "state-checkpoint"], "node": "driver_green"}),
        ScenarioStep(step_id="reconnect", surface="live", instruction="Disconnect, reload durable state, then reconnect after the stream barrier.", expected={"durable_first": True, "phase": "driver_green", "barrier_sequence": 3}),
        ScenarioStep(step_id="interrupt", surface="action", instruction="Resolve the simulated interrupt through the adapter action.", expected={"action_id": "resolve-interrupt", "result": "in-progress"}),
        ScenarioStep(step_id="candidate", surface="intel", instruction="Review and promote the immutable Recon candidate.", expected={"candidate_id": "INS-BENCHMARK-SCOPE", "disposition": "promoted"}),
        ScenarioStep(step_id="correction", surface="action", instruction="Queue a Correction for the next Driver GREEN attempt.", expected={"action_id": "queue-correction", "target": "driver_green", "timing": "next-attempt"}),
        ScenarioStep(step_id="design", surface="action", instruction="Queue a Design decision for the next Architect attempt.", expected={"action_id": "queue-design-decision", "target": "architect", "timing": "next-attempt"}),
        ScenarioStep(step_id="provider-guard", surface="action", instruction="Complete without credentials or provider traffic.", expected={"provider_mode": "disabled", "provider_calls": 0}),
    ]
    measurements = [
        MeasurementCategory(category="packaging", observations=["installed bytes", "artifact count", "clean-machine launch"], method="Build release artifacts from a clean checkout; record commands, hashes, sizes, and missing-runtime behavior.", unit="bytes"),
        MeasurementCategory(category="process", observations=["process tree", "worker isolation", "orphan cleanup"], method="Capture the process tree at idle, during the live step, after simulated failure, and after exit."),
        MeasurementCategory(category="resource", observations=["startup time", "idle working set", "active working set", "CPU"], method="Run five cold starts and five scenario passes; report every sample plus median, environment, and collection tool.", unit="milliseconds and bytes"),
        MeasurementCategory(category="accessibility", observations=["keyboard completion", "focus order", "screen-reader names", "contrast"], method="Complete every scenario step without a pointer and record automated and manual accessibility findings."),
        MeasurementCategory(category="testability", observations=["headless coverage", "determinism", "failure diagnostics"], method="Run the shared acceptance validator three times and record framework-only test code and diagnostic quality."),
        MeasurementCategory(category="failure-recovery", observations=["worker crash", "client restart", "missed transient event", "malformed fixture"], method="Inject each failure at the documented scenario step and record visible state, durable reload, and recovery time."),
        MeasurementCategory(category="permission-surface", observations=["filesystem grants", "process grants", "network grants", "renderer capabilities"], method="Inventory effective production permissions and demonstrate denial of undeclared file, shell, and network access."),
        MeasurementCategory(category="learning", observations=["new concepts", "blocked time", "debugging time", "confidence"], method="Keep a timestamped learning log separating prior familiarity, documentation time, experiment time, and unresolved questions.", unit="minutes"),
        MeasurementCategory(category="implementation-complexity", observations=["framework-specific LOC", "boundary adapters", "configuration files", "dependencies"], method="Count changed framework-only files and lines, classify boundary code, and list dependencies and maintenance obligations.", unit="files and lines"),
    ]
    return BenchmarkBundle(
        generated_at=FIXED_NOW,
        projects=[
            FakeProject(
                project_id=PROJECT_ID,
                name="Signal Lantern",
                path_hint="<fixture>/signal-lantern",
                tickets=[
                    {"ticket_id": "BTN-101", "title": "Deterministic widget", "status": "done"},
                    {"ticket_id": "BTN-102", "title": "Operator checkpoint", "status": "in-progress"},
                ],
            ),
            FakeProject(
                project_id=SECOND_PROJECT_ID,
                name="Quiet Harbor",
                path_hint="<fixture>/quiet-harbor",
                tickets=[
                    {"ticket_id": "BTN-103", "title": "Queued work", "status": "not-started"},
                ],
            ),
        ],
        runs=[completed, active],
        candidates=[candidate],
        observations=observations,
        actions=actions,
        scenario=scenario,
        measurements=measurements,
    )


def write_bundle(output: str | Path) -> tuple[Path, Path, Path]:
    """Export stable JSON inputs; no provider or credential lookup occurs."""
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle()
    fixture_path = root / "fixture.json"
    scenario_path = root / "scenario.json"
    measurement_path = root / "measurement-template.json"
    fixture_path.write_text(
        json.dumps(bundle.model_dump(mode="json", exclude={"scenario", "measurements"}), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    scenario_path.write_text(
        json.dumps([item.model_dump(mode="json") for item in bundle.scenario], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    measurement_path.write_text(
        json.dumps([item.model_dump(mode="json") for item in bundle.measurements], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return fixture_path, scenario_path, measurement_path


def _matches(expected: Any, observed: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(observed, dict) and all(
            key in observed and _matches(value, observed[key])
            for key, value in expected.items()
        )
    return expected == observed


def validate_trace(trace: BenchmarkTrace, bundle: BenchmarkBundle | None = None) -> None:
    """Require one ordered observation matching every shared scenario step."""
    control = bundle or build_bundle()
    expected_ids = [step.step_id for step in control.scenario]
    actual_ids = [entry.step_id for entry in trace.entries]
    if actual_ids != expected_ids:
        raise ValueError(f"trace steps must be {expected_ids}; received {actual_ids}")
    for step, entry in zip(control.scenario, trace.entries, strict=True):
        if not _matches(step.expected, entry.observed):
            raise ValueError(
                f"step {step.step_id!r} did not match expected evidence: {step.expected!r}"
            )
