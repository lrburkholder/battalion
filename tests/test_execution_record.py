"""BTN-19 durable execution record and provenance acceptance tests."""
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from battalion.graph import build_graph, resume_ticket
from battalion.llm.litellm_client import NodeLLMConfig
from battalion.state.models import (
    Budget, CheckpointType, ExecutionRecord, NodeExecution, RunState, RunStatus,
)
from battalion.state.persistence import load_state, save_state


def _state() -> RunState:
    return RunState(
        schema_version="1.0",
        run_id="run-BTN-19",
        ticket_id="BTN-19",
        spec="Persist bounded execution evidence.",
        status=RunStatus.NOT_STARTED,
        phase="architect",
        write_scope={
            "architect": ["plan.md"],
            "driver_red": ["tests/"],
            "driver_green": ["battalion/"],
            "refactorer": ["battalion/"],
            "reviewer": [],
        },
        retry_bound=2,
        budget=Budget(limit=100),
    )


def _configs():
    return {
        "architect": NodeLLMConfig(model="architect-model"),
        "driver": NodeLLMConfig(model="driver-model"),
        "reviewer": NodeLLMConfig(model="reviewer-model"),
        "refactorer": NodeLLMConfig(model="refactorer-model"),
    }


def test_complete_graph_run_records_every_node_and_artifact(tmp_path):
    def architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
        Path(base_dir, "plan.md").write_text("approved plan", encoding="utf-8")
        return state.model_copy(update={"phase": "driver", "status": RunStatus.IN_PROGRESS})

    def driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
        if mode == "red":
            target = Path(base_dir, "tests", "test_widget.py")
            content = "def test_widget(): assert False\n"
        else:
            target = Path(base_dir, "battalion", "widget.py")
            content = "def widget(): return True\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return state.model_copy(update={"phase": "reviewer", "status": RunStatus.IN_PROGRESS})

    def reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
        phase = {
            CheckpointType.RED_CHECK: "driver_green",
            CheckpointType.GREEN_CHECK: "refactorer",
            CheckpointType.REFACTOR_CHECK: "done",
        }[checkpoint]
        status = RunStatus.DONE if phase == "done" else RunStatus.IN_PROGRESS
        return state.model_copy(update={"phase": phase, "status": status})

    def refactorer(state, refactor_text, llm_config, base_dir, prompts_dir=None):
        Path(base_dir, "battalion", "widget.py").write_text(
            "def widget() -> bool: return True\n", encoding="utf-8"
        )
        return state.model_copy(update={"phase": "reviewer", "status": RunStatus.IN_PROGRESS})

    with patch("battalion.nodes.architect.run_architect", side_effect=architect), \
         patch("battalion.nodes.driver.run_driver", side_effect=driver), \
         patch("battalion.nodes.reviewer.run_reviewer", side_effect=reviewer), \
         patch("battalion.nodes.refactorer.run_refactorer", side_effect=refactorer):
        raw = build_graph(_configs(), base_dir=tmp_path).compile().invoke(
            _state(), {"recursion_limit": 10}
        )

    final = RunState.model_validate(raw)
    executions = final.execution_record.node_executions
    assert final.status == RunStatus.DONE
    assert [item.phase for item in executions] == [
        "architect", "driver_red", "reviewer_red", "driver_green",
        "reviewer_green", "refactorer", "reviewer_refactor",
    ]
    assert [item.model_identity for item in executions] == [
        "architect-model", "driver-model", "reviewer-model", "driver-model",
        "reviewer-model", "refactorer-model", "reviewer-model",
    ]
    assert all(item.started_at <= item.ended_at for item in executions)
    assert all(item.input_references for item in executions)
    reviews = [item for item in executions if item.role == "reviewer"]
    assert all(item.review_result.verdict == "accepted" for item in reviews)
    assert all(item.test_outcome.accepted for item in reviews)

    artifacts = [artifact for item in executions for artifact in item.artifact_provenance]
    assert {item.path for item in artifacts} == {
        "plan.md", "tests/test_widget.py", "battalion/widget.py"
    }
    assert all(item.originating_run_id == final.run_id for item in artifacts)
    assert all(
        item.originating_node_execution_id
        in {execution.execution_id for execution in executions}
        for item in artifacts
    )

    path = tmp_path / "run.json"
    save_state(final, path)
    assert load_state(path) == final


def test_execution_record_format_is_versioned_and_validated():
    assert ExecutionRecord().schema_version == "1.2"
    assert ExecutionRecord(schema_version="1.0").schema_version == "1.0"
    assert ExecutionRecord(schema_version="1.1").schema_version == "1.1"
    with pytest.raises(ValidationError):
        ExecutionRecord(schema_version="2.0")


def test_execution_record_survives_pause_save_load_and_resume(tmp_path):
    def architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
        return state.model_copy(update={"phase": "driver", "status": RunStatus.IN_PROGRESS})

    def driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
        return state.model_copy(update={"phase": "reviewer", "status": RunStatus.IN_PROGRESS})

    def reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
        phase = {
            CheckpointType.RED_CHECK: "driver_green",
            CheckpointType.GREEN_CHECK: "refactorer",
            CheckpointType.REFACTOR_CHECK: "done",
        }[checkpoint]
        return state.model_copy(update={
            "phase": phase,
            "status": RunStatus.DONE if phase == "done" else RunStatus.IN_PROGRESS,
        })

    def refactorer(state, refactor_text, llm_config, base_dir, prompts_dir=None):
        return state.model_copy(update={"phase": "reviewer", "status": RunStatus.IN_PROGRESS})

    initial = _state().model_copy(update={"manual_checkpoints": ["driver"]})
    patches = (
        patch("battalion.nodes.architect.run_architect", side_effect=architect),
        patch("battalion.nodes.driver.run_driver", side_effect=driver),
        patch("battalion.nodes.reviewer.run_reviewer", side_effect=reviewer),
        patch("battalion.nodes.refactorer.run_refactorer", side_effect=refactorer),
    )
    with patches[0], patches[1], patches[2], patches[3]:
        paused = RunState.model_validate(
            build_graph(_configs(), base_dir=tmp_path).compile().invoke(initial)
        )

    assert paused.status == RunStatus.AWAITING_HUMAN
    assert len(paused.execution_record.node_executions) == 1
    architect_execution = paused.execution_record.node_executions[0]
    assert architect_execution.outcome == "interrupted"
    assert paused.interrupt_log[0].node_execution_id == architect_execution.execution_id

    path = tmp_path / "paused.json"
    save_state(paused, path)
    loaded = load_state(path).model_copy(update={"manual_checkpoints": []})
    patches = (
        patch("battalion.nodes.architect.run_architect", side_effect=architect),
        patch("battalion.nodes.driver.run_driver", side_effect=driver),
        patch("battalion.nodes.reviewer.run_reviewer", side_effect=reviewer),
        patch("battalion.nodes.refactorer.run_refactorer", side_effect=refactorer),
    )
    with patches[0], patches[1], patches[2], patches[3]:
        resumed = RunState.model_validate(
            resume_ticket(loaded, _configs(), base_dir=tmp_path)
        )

    assert resumed.status == RunStatus.DONE
    assert len(resumed.execution_record.node_executions) == 7
    assert resumed.execution_record.node_executions[0] == architect_execution


def test_input_references_are_bounded():
    with pytest.raises(ValidationError):
        NodeExecution.model_validate({
            "execution_id": "node-too-many-inputs",
            "role": "architect",
            "phase": "architect",
            "model_identity": "model",
            "input_references": [
                {"kind": "state", "reference": f"ref-{index}"}
                for index in range(21)
            ],
            "started_at": "2026-08-13T00:00:00Z",
            "ended_at": "2026-08-13T00:00:01Z",
            "outcome": "succeeded",
        })
