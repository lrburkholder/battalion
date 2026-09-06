"""BTN-19 durable execution record and provenance acceptance tests."""
from support.state import make_run_state
from support.graph import patched_nodes, reviewer_accepting

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from battalion.graph import build_graph, resume_ticket
from battalion.execution import ExecutionCapture, record_role_result
from battalion.llm.litellm_client import NodeLLMConfig
from battalion.role_results import (
    DriverReasonCode,
    RoleResultEvidenceReference,
    RoleResultKind,
    RoleResultRejected,
    RoleResultSubmission,
    submit_role_result,
)
from battalion.state.models import (
    CheckpointType,
    CodeProvenance,
    EvidenceReference,
    ExecutionRecord,
    NodeExecution,
    RunState,
    RunStatus,
)
from battalion.state.persistence import load_state, save_state


def _state() -> RunState:
    return make_run_state(
        run_id='run-BTN-19',
        ticket_id='BTN-19',
        spec='Persist bounded execution evidence.',
        write_scope={
            "architect": ["plan.md"], "driver_red": ["tests/"],
            "driver_green": ["battalion/"], "refactorer": ["battalion/"], "reviewer": [],
        },
    )


def _configs():
    return {
        "architect": NodeLLMConfig(model="architect-model"),
        "driver": NodeLLMConfig(model="driver-model"),
        "reviewer": NodeLLMConfig(model="reviewer-model"),
        "refactorer": NodeLLMConfig(model="refactorer-model"),
    }


def _architect_stub(state, spec_text, llm_config, base_dir, prompts_dir=None):
    Path(base_dir, "plan.md").write_text("approved plan", encoding="utf-8")
    return state.model_copy(update={"phase": "driver", "status": RunStatus.IN_PROGRESS})


def _driver_stub(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
    if mode == "red":
        target = Path(base_dir, "tests", "test_widget.py")
        content = "def test_widget(): assert False\n"
    else:
        target = Path(base_dir, "battalion", "widget.py")
        content = "def widget(): return True\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return state.model_copy(update={"phase": "reviewer", "status": RunStatus.IN_PROGRESS})


def _refactorer_stub(state, refactor_text, llm_config, base_dir, prompts_dir=None):
    Path(base_dir, "battalion", "widget.py").write_text(
        "def widget() -> bool: return True\n", encoding="utf-8"
    )
    return state.model_copy(update={"phase": "reviewer", "status": RunStatus.IN_PROGRESS})


@pytest.fixture
def stub_graph_nodes():
    with patched_nodes(
        architect=_architect_stub, driver=_driver_stub,
        reviewer=reviewer_accepting(), refactorer=_refactorer_stub,
    ):
        yield


def test_complete_graph_run_records_every_node_and_artifact(tmp_path, stub_graph_nodes):
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
    assert ExecutionRecord().schema_version == "1.7"
    assert ExecutionRecord(schema_version="1.0").schema_version == "1.0"
    assert ExecutionRecord(schema_version="1.1").schema_version == "1.1"
    assert ExecutionRecord(schema_version="1.2").schema_version == "1.2"
    assert ExecutionRecord(schema_version="1.3").schema_version == "1.3"
    assert ExecutionRecord(schema_version="1.4").schema_version == "1.4"
    assert ExecutionRecord(schema_version="1.5").schema_version == "1.5"
    assert ExecutionRecord(schema_version="1.6").schema_version == "1.6"
    with pytest.raises(ValidationError):
        ExecutionRecord(schema_version="2.0")


def test_role_result_evidence_must_match_the_capture_inputs(tmp_path):
    state = _state()
    result = submit_role_result(
        RoleResultSubmission(
            kind=RoleResultKind.ESCALATED,
            reason_code=DriverReasonCode.SPECIFICATION_AMBIGUITY,
            summary="The accepted plan conflicts with the ticket objective.",
            evidence_refs=[
                RoleResultEvidenceReference(kind="artifact", reference="plan.md")
            ],
        ),
        role="driver",
        mode="red",
    )
    capture = ExecutionCapture.start(state, "driver_red", "driver-model", tmp_path)
    record_role_result(result)
    completed = capture.finish(
        state,
        state.model_copy(update={"phase": "awaiting_human", "status": RunStatus.AWAITING_HUMAN}),
    )
    assert completed.execution_record.node_executions[-1].role_result == result

    capture = ExecutionCapture.start(state, "driver_red", "driver-model", tmp_path)
    result_with_unknown_evidence = result.model_copy(update={
        "evidence_refs": [
            RoleResultEvidenceReference(kind="artifact", reference="unseen.md")
        ]
    })
    with pytest.raises(RoleResultRejected, match="not supplied"):
        record_role_result(result_with_unknown_evidence)
    capture.finish(state, state)


@pytest.mark.parametrize("configuration_kind", ["legacy-raw", "environment-reference"])
def test_new_execution_evidence_is_bounded_and_legacy_records_remain_compatible(tmp_path, monkeypatch, configuration_kind):
    legacy = NodeExecution.model_validate({
        "execution_id": "node-legacy",
        "role": "architect",
        "phase": "architect",
        "model_identity": "model",
        "input_references": [{"kind": "state", "reference": "RunState.spec"}],
        "started_at": "2026-08-13T00:00:00Z",
        "ended_at": "2026-08-13T00:00:01Z",
        "outcome": "succeeded",
    })
    assert legacy.operator_summary is None
    assert legacy.prompt_provenance is None
    assert legacy.code_provenance is None

    state = _state().model_copy(update={"spec": "x" * 1_100_000})
    if configuration_kind == "legacy-raw":
        # Retain the historical negative case at the evidence boundary even
        # though new NodeLLMConfig instances now reject inline secrets.
        config = {"model": "architect-model", "extra_params": {"api_key": "must-not-be-retained"}}
    else:
        monkeypatch.setenv("ARCHITECT_TOKEN", "must-not-be-retained")
        config = NodeLLMConfig(model="architect-model", api_key_env="ARCHITECT_TOKEN")
        assert config.request_params()["api_key"] == "must-not-be-retained"
    capture = ExecutionCapture.start(
        state, "architect", "architect-model", tmp_path, model_configuration=config
    )
    completed = capture.finish(
        state,
        state.model_copy(update={"phase": "driver", "status": RunStatus.IN_PROGRESS}),
    )
    execution = completed.execution_record.node_executions[-1]
    reference = execution.input_references[0]
    assert reference.sha256 is not None
    assert reference.hash_algorithm == "sha256"
    assert reference.inclusion_reason
    assert reference.truncated is True
    assert reference.observed_bytes == 1_100_000
    assert reference.hashed_bytes < reference.observed_bytes
    assert execution.operator_summary.last_node == "architect"
    assert execution.operator_summary.what_should_happen_next == "Continue at phase driver."
    assert execution.prompt_provenance.contract_version == "architect/v1"
    assert execution.prompt_provenance.template_path == "battalion/prompts/architect.md"
    assert len(execution.prompt_provenance.template_hash) == 64
    assert len(execution.prompt_provenance.model_configuration_identity) == 64
    assert "must-not-be-retained" not in execution.model_dump_json()
    assert execution.code_provenance.repository_available is False

    with pytest.raises(ValidationError, match="context digest metadata must be complete"):
        EvidenceReference(
            kind="state", reference="RunState.spec", sha256="0" * 64
        )
    with pytest.raises(ValidationError, match="complete repository evidence"):
        CodeProvenance(repository_available=True)


def test_dirty_git_workspace_is_explicitly_not_exactly_reconstructable(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "btn-33-test")
    git("config", "user.email", "battalion-tests@example.invalid")
    git("config", "user.name", "Battalion Tests")
    plan = tmp_path / "plan.md"
    plan.write_text("initial plan", encoding="utf-8")
    git("add", "plan.md")
    git("commit", "-m", "initial")
    base_commit = git("rev-parse", "HEAD")

    state = _state()
    capture = ExecutionCapture.start(
        state,
        "architect",
        "architect-model",
        tmp_path,
        model_configuration=NodeLLMConfig(model="architect-model"),
    )
    plan.write_text("changed plan", encoding="utf-8")
    completed = capture.finish(
        state,
        state.model_copy(update={"phase": "driver", "status": RunStatus.IN_PROGRESS}),
    )
    provenance = completed.execution_record.node_executions[-1].code_provenance
    assert provenance.base_commit_object_id == base_commit
    assert provenance.object_id_algorithm in {"sha1", "sha256"}
    assert provenance.branch == "btn-33-test"
    assert provenance.detached is False
    assert provenance.dirty_at_start is False
    assert provenance.dirty_at_end is True
    assert provenance.exact_workspace_reconstructable is False
    assert provenance.reconstruction_limitation == "dirty-workspace-patch-not-retained"


def test_execution_record_survives_pause_save_load_and_resume(
    tmp_path, stub_graph_nodes
):
    initial = _state().model_copy(update={"manual_checkpoints": ["driver"]})
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
