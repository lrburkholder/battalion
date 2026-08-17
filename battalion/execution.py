"""Capture bounded, durable node-execution evidence (BTN-19)."""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from battalion.state.models import (
    ArtifactProvenance,
    CheckpointType,
    EvidenceReference,
    ExecutionRecord,
    LLMCallCost,
    NodeExecution,
    ReviewResult,
    RunState,
    TestOutcome,
    ToolActivity,
)

_ACTIVE_CAPTURE: ContextVar["ExecutionCapture | None"] = ContextVar(
    "battalion_execution_capture", default=None
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _role(node_name: str) -> str:
    if node_name.startswith("driver_"):
        return "driver"
    if node_name.startswith("reviewer_"):
        return "reviewer"
    return node_name


def _input_references(node_name: str) -> list[EvidenceReference]:
    if node_name == "architect":
        return [EvidenceReference(kind="state", reference="RunState.spec")]
    if node_name == "driver_red":
        return [
            EvidenceReference(kind="artifact", reference="plan.md"),
            EvidenceReference(kind="workspace", reference="implementation roots"),
        ]
    if node_name == "driver_green":
        return [
            EvidenceReference(kind="artifact", reference="plan.md"),
            EvidenceReference(kind="workspace", reference="test roots"),
        ]
    if node_name == "refactorer":
        return [
            EvidenceReference(kind="artifact", reference="plan.md"),
            EvidenceReference(kind="workspace", reference="test and implementation roots"),
        ]
    return [EvidenceReference(kind="workspace", reference="clean project snapshot")]


def _scope_entries(state: RunState, node_name: str) -> list[str]:
    if node_name == "architect":
        return state.write_scope.get("architect", [])
    if node_name in {"driver_red", "driver_green", "refactorer"}:
        return state.write_scope.get(node_name, state.write_scope.get("driver", []))
    return []


def _snapshot(base_dir: Path, entries: list[str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for entry in entries:
        target = base_dir / entry.rstrip("/")
        paths = target.rglob("*") if target.is_dir() else [target]
        for path in paths:
            if path.is_file():
                relative = path.relative_to(base_dir).as_posix()
                snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


@dataclass
class ExecutionCapture:
    execution_id: str
    node_name: str
    model_identity: str
    started_at: datetime
    before_files: dict[str, str]
    base_dir: Path
    written_paths: set[str]
    llm_calls: list[LLMCallCost]

    @classmethod
    def start(
        cls, state: RunState, node_name: str, model_identity: str, base_dir: str | Path
    ) -> "ExecutionCapture":
        root = Path(base_dir).resolve()
        capture = cls(
            execution_id=f"node-{uuid4()}",
            node_name=node_name,
            model_identity=model_identity,
            started_at=_utcnow(),
            before_files=_snapshot(root, _scope_entries(state, node_name)),
            base_dir=root,
            written_paths=set(),
            llm_calls=[],
        )
        _ACTIVE_CAPTURE.set(capture)
        return capture

    def finish(
        self,
        old_state: RunState,
        new_state: RunState,
        *,
        checkpoint: CheckpointType | None = None,
    ) -> RunState:
        after_files = _snapshot(
            self.base_dir, _scope_entries(old_state, self.node_name)
        )
        _ACTIVE_CAPTURE.set(None)
        changed = {
            path: digest
            for path, digest in after_files.items()
            if self.before_files.get(path) != digest
        }
        for path in self.written_paths:
            if path in after_files:
                changed[path] = after_files[path]
        artifacts = [
            ArtifactProvenance(
                path=path,
                sha256=digest,
                originating_run_id=old_state.run_id,
                originating_node_execution_id=self.execution_id,
            )
            for path, digest in sorted(changed.items())
        ]
        tools = [
            ToolActivity(tool="scoped-write", action="write", target=path, outcome="succeeded")
            for path in sorted(changed)
        ]

        new_interrupt_indexes = list(
            range(len(old_state.interrupt_log), len(new_state.interrupt_log))
        )
        interrupts = list(new_state.interrupt_log)
        for index in new_interrupt_indexes:
            interrupts[index] = interrupts[index].model_copy(
                update={"node_execution_id": self.execution_id}
            )

        interrupted = bool(new_interrupt_indexes)
        review = None
        test = None
        verdict = None
        outcome = "interrupted" if interrupted else "succeeded"
        output_reference = artifacts[0].path if len(artifacts) == 1 else None

        if checkpoint is not None:
            expected_to_pass = checkpoint != CheckpointType.RED_CHECK
            accepted_phase = {
                CheckpointType.RED_CHECK: "driver_green",
                CheckpointType.GREEN_CHECK: "refactorer",
                CheckpointType.REFACTOR_CHECK: "done",
            }[checkpoint]
            accepted = new_state.phase == accepted_phase and not interrupted
            passed = expected_to_pass if accepted else not expected_to_pass
            cause = None
            if not accepted and len(new_state.reviewer_rejection_history) > len(
                old_state.reviewer_rejection_history
            ):
                cause = new_state.reviewer_rejection_history[-1].cause[:2000]
            review = ReviewResult(
                checkpoint=checkpoint,
                verdict="accepted" if accepted else "rejected",
                cause=cause,
            )
            test = TestOutcome(
                checkpoint=checkpoint,
                passed=passed,
                expected_to_pass=expected_to_pass,
                accepted=accepted,
            )
            tools.append(
                ToolActivity(tool="pytest", action="execute", outcome="succeeded")
            )
            verdict = review.verdict if cause is None else cause
            if not accepted and not interrupted:
                outcome = "rejected"
            output_reference = f"review:{checkpoint.value}"
        elif interrupted:
            verdict = new_state.interrupt_log[-1].trigger
        elif artifacts:
            output_reference = ",".join(item.path for item in artifacts)[:1000]
        else:
            output_reference = f"state:phase={new_state.phase}"

        execution = NodeExecution(
            execution_id=self.execution_id,
            role=_role(self.node_name),
            phase=self.node_name,
            model_identity=self.model_identity,
            input_references=_input_references(self.node_name),
            output_reference=output_reference,
            verdict=verdict,
            started_at=self.started_at,
            ended_at=_utcnow(),
            outcome=outcome,
            tool_activity=tools,
            test_outcome=test,
            review_result=review,
            artifact_provenance=artifacts,
            interrupt_ids=new_interrupt_indexes,
            llm_calls=list(self.llm_calls),
        )
        record = new_state.execution_record.model_copy(
            update={
                "schema_version": "1.2",
                "node_executions": new_state.execution_record.node_executions
                + [execution]
            }
        )
        return new_state.model_copy(
            update={"interrupt_log": interrupts, "execution_record": record}
        )


def record_scoped_write(target: Path) -> None:
    """Link a bound write tool action to the currently executing node."""
    capture = _ACTIVE_CAPTURE.get()
    if capture is None:
        return
    resolved = target.resolve()
    try:
        relative = resolved.relative_to(capture.base_dir).as_posix()
    except ValueError:
        return
    capture.written_paths.add(relative)


def record_llm_call(call: LLMCallCost) -> None:
    """Attach one provider completion's usage to the active node execution."""
    capture = _ACTIVE_CAPTURE.get()
    if capture is not None:
        capture.llm_calls.append(call)


def summarize_costs(record: ExecutionRecord) -> dict[str, object]:
    """Build a deterministic per-phase and whole-run cost projection."""
    phases: dict[str, dict[str, object]] = {}
    for execution in record.node_executions:
        for call in execution.llm_calls:
            phase = phases.setdefault(
                execution.phase,
                {
                    "phase": execution.phase,
                    "role": execution.role,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "known_costs": {},
                    "cost_sources": {},
                    "unknown_cost_calls": 0,
                },
            )
            phase["calls"] += 1
            phase["input_tokens"] += call.input_tokens
            phase["output_tokens"] += call.output_tokens
            if call.cost is None:
                phase["unknown_cost_calls"] += 1
                continue
            currency = call.cost_currency
            known_costs = phase["known_costs"]
            known_costs[currency] = known_costs.get(currency, Decimal("0")) + call.cost
            sources = phase["cost_sources"].setdefault(currency, set())
            sources.add(call.cost_source.value)

    ordered = []
    for phase_name in sorted(phases):
        phase = phases[phase_name]
        phase["costs"] = [
            {
                "amount": str(phase["known_costs"][currency]),
                "currency": currency,
                "sources": sorted(phase["cost_sources"][currency]),
            }
            for currency in sorted(phase["known_costs"])
        ]
        del phase["known_costs"]
        del phase["cost_sources"]
        ordered.append(phase)

    total_costs: dict[str, Decimal] = {}
    total_sources: dict[str, set[str]] = {}
    for execution in record.node_executions:
        for call in execution.llm_calls:
            if call.cost is None:
                continue
            currency = call.cost_currency
            total_costs[currency] = total_costs.get(currency, Decimal("0")) + call.cost
            total_sources.setdefault(currency, set()).add(call.cost_source.value)
    return {
        "calls": sum(item["calls"] for item in ordered),
        "input_tokens": sum(item["input_tokens"] for item in ordered),
        "output_tokens": sum(item["output_tokens"] for item in ordered),
        "costs": [
            {
                "amount": str(total_costs[currency]),
                "currency": currency,
                "sources": sorted(total_sources[currency]),
            }
            for currency in sorted(total_costs)
        ],
        "unknown_cost_calls": sum(item["unknown_cost_calls"] for item in ordered),
        "phases": ordered,
    }
