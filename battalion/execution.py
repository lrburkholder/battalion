"""Capture bounded, durable node-execution evidence (BTN-19)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from battalion.prompts.loader import DEFAULT_PROMPTS_DIR, prompt_contract_version
from battalion.state.models import (
    ArtifactProvenance,
    CheckpointType,
    CodeProvenance,
    EvidenceReference,
    ExecutionRecord,
    LLMCallCost,
    NodeExecution,
    OperatorSummary,
    PromptProvenance,
    ReviewResult,
    RunState,
    TestOutcome,
    ToolActivity,
)

_BATTALION_ROOT = Path(__file__).resolve().parents[1]
_MAX_CONTEXT_BYTES = 1_048_576
_MAX_CONTEXT_FILES = 200

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


def _declared_input_references(node_name: str) -> list[tuple[str, str, str]]:
    if node_name == "architect":
        return [("state", "RunState.spec", "ticket objective supplied to Architect")]
    if node_name == "driver_red":
        return [
            ("artifact", "plan.md", "accepted implementation plan"),
            ("workspace", "implementation roots", "code surface available to RED Driver"),
        ]
    if node_name == "driver_green":
        return [
            ("artifact", "plan.md", "accepted implementation plan"),
            ("workspace", "test roots", "failing tests available to GREEN Driver"),
        ]
    if node_name == "refactorer":
        return [
            ("artifact", "plan.md", "accepted implementation plan"),
            ("workspace", "test and implementation roots", "behavior-preserving refactor context"),
        ]
    return [("workspace", "clean project snapshot", "Reviewer verification snapshot")]


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


def _bounded_digest(data: bytes) -> tuple[str, bool, int, int]:
    observed = len(data)
    bounded = data[:_MAX_CONTEXT_BYTES]
    return hashlib.sha256(bounded).hexdigest(), observed > len(bounded), observed, len(bounded)


def _workspace_context_digest(
    base_dir: Path, entries: list[str]
) -> tuple[str, bool, int, int]:
    candidates: list[Path] = []
    roots = [base_dir / item.rstrip("/") for item in entries] if entries else [base_dir]
    for root in roots:
        paths = root.rglob("*") if root.is_dir() else [root]
        candidates.extend(path for path in paths if path.is_file())
    resolved_root = base_dir.resolve()
    unique = sorted(
        {
            path.resolve()
            for path in candidates
            if ".git" not in path.parts
            and ".battalion" not in path.parts
            and path.resolve().is_relative_to(resolved_root)
        },
        key=lambda path: path.as_posix(),
    )
    truncated = len(unique) > _MAX_CONTEXT_FILES
    manifest: list[dict[str, object]] = []
    observed_bytes = sum(path.stat().st_size for path in unique)
    hashed_bytes = 0
    for path in unique[:_MAX_CONTEXT_FILES]:
        size = path.stat().st_size
        remaining = _MAX_CONTEXT_BYTES - hashed_bytes
        if remaining <= 0:
            truncated = True
            break
        with path.open("rb") as handle:
            bounded = handle.read(remaining)
        hashed_bytes += len(bounded)
        truncated = truncated or size > len(bounded)
        try:
            reference = path.relative_to(base_dir).as_posix()
        except ValueError:
            reference = path.name
        manifest.append({
            "path": reference,
            "sha256": hashlib.sha256(bounded).hexdigest(),
            "observed_bytes": size,
            "hashed_bytes": len(bounded),
        })
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), truncated, observed_bytes, hashed_bytes


def _input_references(
    state: RunState, node_name: str, base_dir: Path
) -> list[EvidenceReference]:
    references: list[EvidenceReference] = []
    for kind, reference, reason in _declared_input_references(node_name):
        if kind == "state":
            digest, truncated, observed, hashed = _bounded_digest(state.spec.encode())
        elif kind == "artifact":
            path = base_dir / reference
            data = path.read_bytes() if path.is_file() else b""
            digest, truncated, observed, hashed = _bounded_digest(data)
        else:
            digest, truncated, observed, hashed = _workspace_context_digest(
                base_dir, _scope_entries(state, node_name)
            )
        references.append(EvidenceReference(
            kind=kind,
            reference=reference,
            sha256=digest,
            hash_algorithm="sha256",
            inclusion_reason=reason,
            truncated=truncated,
            observed_bytes=observed,
            hashed_bytes=hashed,
        ))
    return references


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            check=False, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git_start(root: Path) -> dict[str, object]:
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        return {"repository_available": False}
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    status = _git(root, "status", "--porcelain=v1")
    dirty = True if status is None else bool(status)
    return {
        "repository_available": True,
        "base_commit_object_id": commit,
        "object_id_algorithm": "sha256" if len(commit) == 64 else "sha1",
        "branch": branch,
        "detached": branch is None,
        "dirty_at_start": dirty,
    }


def _configuration_identity(configuration: Any, model_identity: str) -> str:
    if configuration is None:
        value: Any = {"model": model_identity}
    elif is_dataclass(configuration):
        value = asdict(configuration)
    else:
        value = configuration
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr).encode()
    return hashlib.sha256(encoded).hexdigest()


def _prompt_name(node_name: str) -> str:
    if node_name.startswith("reviewer_"):
        return "reviewer"
    return node_name.replace("_", "-")


def _prompt_provenance(
    node_name: str,
    prompts_dir: str | Path | None,
    model_configuration: Any,
    model_identity: str,
) -> PromptProvenance | None:
    name = _prompt_name(node_name)
    path = (Path(prompts_dir) if prompts_dir is not None else DEFAULT_PROMPTS_DIR) / f"{name}.md"
    if not path.is_file():
        return None
    revision = _git(_BATTALION_ROOT, "rev-parse", "HEAD")
    resolved_path = path.resolve()
    try:
        template_path = resolved_path.relative_to(_BATTALION_ROOT).as_posix()
    except ValueError:
        template_path = resolved_path.as_posix()
    return PromptProvenance(
        template_identity=name,
        template_path=template_path,
        contract_version=prompt_contract_version(name),
        template_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        battalion_revision=revision,
        model_configuration_identity=_configuration_identity(
            model_configuration, model_identity
        ),
    )


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
    streamed_reasoning_characters: int
    streamed_content_characters: int
    no_change_reason: str | None
    input_references: list[EvidenceReference]
    prompt_provenance: PromptProvenance | None
    code_start: dict[str, object]

    @classmethod
    def start(
        cls,
        state: RunState,
        node_name: str,
        model_identity: str,
        base_dir: str | Path,
        *,
        prompts_dir: str | Path | None = None,
        model_configuration: Any = None,
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
            streamed_reasoning_characters=0,
            streamed_content_characters=0,
            no_change_reason=None,
            input_references=_input_references(state, node_name, root),
            prompt_provenance=_prompt_provenance(
                node_name, prompts_dir, model_configuration, model_identity
            ),
            code_start=_git_start(root),
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
        elif self.no_change_reason is not None:
            output_reference = "refactorer:no-change"
        else:
            output_reference = f"state:phase={new_state.phase}"

        code_data = dict(self.code_start)
        if code_data["repository_available"]:
            end_status = _git(self.base_dir, "status", "--porcelain=v1")
            dirty_end = True if end_status is None else bool(end_status)
            dirty = bool(code_data["dirty_at_start"]) or dirty_end
            code_data.update({
                "dirty_at_end": dirty_end,
                "exact_workspace_reconstructable": not dirty,
                "reconstruction_limitation": (
                    "dirty-workspace-patch-not-retained" if dirty else None
                ),
            })
        code_provenance = CodeProvenance.model_validate(code_data)
        verification = []
        if test is not None:
            verification.append(
                f"{test.checkpoint.value}: {'passed' if test.passed else 'failed'}; "
                f"{'accepted' if test.accepted else 'not accepted'}"
            )
        open_questions = []
        if interrupted:
            open_questions.append(
                f"Resolve interrupt: {new_state.interrupt_log[-1].trigger}"
            )
        no_change_detail = (
            f" No code change was warranted: {self.no_change_reason}"
            if self.no_change_reason is not None
            else ""
        )
        summary = OperatorSummary(
            what_i_did=(
                f"{self.node_name} finished with outcome {outcome}; "
                f"recorded {len(artifacts)} changed artifact(s).{no_change_detail}"
            ),
            what_should_happen_next=f"Continue at phase {new_state.phase}.",
            open_questions=open_questions,
            verification_performed=verification,
            artifact_paths=[item.path for item in artifacts],
            last_role=_role(self.node_name),
            last_node=self.node_name,
            last_phase=self.node_name,
        )

        execution = NodeExecution(
            execution_id=self.execution_id,
            role=_role(self.node_name),
            phase=self.node_name,
            model_identity=self.model_identity,
            input_references=self.input_references,
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
            streamed_reasoning_characters=self.streamed_reasoning_characters,
            streamed_content_characters=self.streamed_content_characters,
            operator_summary=summary,
            prompt_provenance=self.prompt_provenance,
            code_provenance=code_provenance,
        )
        record = new_state.execution_record.model_copy(
            update={
                "schema_version": "1.3",
                "node_executions": new_state.execution_record.node_executions
                + [execution]
            }
        )
        return new_state.model_copy(
            update={"interrupt_log": interrupts, "execution_record": record}
        )

    def include_human_interventions(self, state: RunState) -> None:
        """Record bounded provenance for interventions delivered to this attempt."""
        for item in state.interventions:
            if item.delivered_to_execution_id != self.execution_id:
                continue
            encoded = item.text.encode("utf-8")
            digest, truncated, observed, hashed = _bounded_digest(encoded)
            self.input_references.append(EvidenceReference(
                kind="state",
                reference=f"RunState.interventions/{item.action_id}",
                sha256=digest,
                hash_algorithm="sha256",
                inclusion_reason=(
                    f"human-supplied {item.kind.value} for {item.target.value}"
                ),
                truncated=truncated,
                observed_bytes=observed,
                hashed_bytes=hashed,
            ))


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


def record_stream_observation(kind: str, content: str) -> None:
    """Count streamed provider text on the active node without retaining it.

    Raw trace text remains optional operator output. These bounded counters make
    a model's visible deliberation comparable by concrete node and model in
    the durable execution record.
    """
    capture = _ACTIVE_CAPTURE.get()
    if capture is None:
        return
    if kind == "reasoning":
        capture.streamed_reasoning_characters += len(content)
    else:
        capture.streamed_content_characters += len(content)


def record_no_change(reason: str) -> None:
    """Record Refactorer's explicit behavior-preserving no-op decision."""
    capture = _ACTIVE_CAPTURE.get()
    if capture is not None:
        capture.no_change_reason = reason


def summarize_costs(record: ExecutionRecord) -> dict[str, object]:
    """Build a deterministic per-node model-usage and cost projection."""
    phases: dict[str, dict[str, object]] = {}
    for execution in record.node_executions:
        if not execution.llm_calls and not (
            execution.streamed_reasoning_characters
            or execution.streamed_content_characters
        ):
            continue
        phase = phases.setdefault(
            execution.phase,
            {
                "phase": execution.phase,
                "role": execution.role,
                "models": set(),
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "streamed_reasoning_characters": 0,
                "streamed_content_characters": 0,
                "known_costs": {},
                "cost_sources": {},
                "unknown_cost_calls": 0,
            },
        )
        phase["models"].add(execution.model_identity)
        phase["streamed_reasoning_characters"] += execution.streamed_reasoning_characters
        phase["streamed_content_characters"] += execution.streamed_content_characters
        for call in execution.llm_calls:
            phase["models"].add(call.model)
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
        phase["models"] = sorted(phase["models"])
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
        "streamed_reasoning_characters": sum(
            item["streamed_reasoning_characters"] for item in ordered
        ),
        "streamed_content_characters": sum(
            item["streamed_content_characters"] for item in ordered
        ),
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
