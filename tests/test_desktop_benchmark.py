"""BTN-37 shared desktop framework benchmark fixture acceptance tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.desktop import BenchmarkTrace, build_bundle, validate_trace, write_bundle


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _passing_trace(framework: str = "tauri") -> BenchmarkTrace:
    bundle = build_bundle()
    return BenchmarkTrace.model_validate({
        "framework": framework,
        "entries": [
            {"step_id": step.step_id, "observed": step.expected}
            for step in bundle.scenario
        ],
    })


def test_fixture_is_deterministic_complete_and_provider_disabled():
    first = build_bundle()
    second = build_bundle()

    assert first == second
    assert first.provider_mode == "disabled"
    assert len(first.projects) == 2
    assert first.model_dump_json() == second.model_dump_json()
    assert {run.status.value for run in first.runs} == {"done", "awaiting-human"}
    assert first.runs[0].execution_record.node_executions
    assert first.runs[0].execution_record.node_executions[1].artifact_provenance
    assert first.runs[1].interrupt_log
    assert first.candidates[0].lifecycle.value == "candidate"
    assert [event.category.value for event in first.observations] == [
        "transient", "action-required", "durable",
    ]
    calls = [
        call
        for run in first.runs
        for execution in run.execution_record.node_executions
        for call in execution.llm_calls
    ]
    assert any(call.cost is not None for call in calls)
    assert any(call.cost is None and call.input_tokens for call in calls)


def test_scenario_covers_every_required_surface_and_operator_action():
    bundle = build_bundle()
    assert [step.step_id for step in bundle.scenario] == [
        "work", "history", "execution", "cost", "provenance", "live",
        "reconnect", "interrupt", "candidate", "correction", "design",
        "provider-guard",
    ]
    assert {action.kind for action in bundle.actions} == {
        "resolve-interrupt", "review-candidate", "correction", "design-decision",
    }
    assert {item.category for item in bundle.measurements} == {
        "packaging", "process", "resource", "accessibility", "testability",
        "failure-recovery", "permission-surface", "learning",
        "implementation-complexity",
    }


@pytest.mark.parametrize("framework", ["tauri", "pyside6", "electron"])
def test_same_acceptance_contract_validates_each_framework(framework):
    validate_trace(_passing_trace(framework))


def test_acceptance_rejects_reordered_missing_or_false_evidence():
    trace = _passing_trace()
    with pytest.raises(ValueError, match="trace steps"):
        validate_trace(trace.model_copy(update={"entries": list(reversed(trace.entries))}))

    entries = list(trace.entries)
    entries[0] = entries[0].model_copy(update={"observed": {"ticket_id": "wrong"}})
    with pytest.raises(ValueError, match="step 'work'"):
        validate_trace(trace.model_copy(update={"entries": entries}))


def test_export_is_stable_and_contains_no_secret_or_provider_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_bundle(first)
    write_bundle(second)

    for name in ("fixture.json", "scenario.json", "measurement-template.json"):
        one = (first / name).read_bytes()
        assert one == (second / name).read_bytes()
        assert b"must-not-appear" not in one
        json.loads(one)


def test_export_and_acceptance_cli_are_shared_and_framework_neutral(tmp_path):
    output = tmp_path / "input"
    export = subprocess.run(
        [sys.executable, "-m", "benchmarks.desktop.export", str(output)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "fixture.json" in export.stdout
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(_passing_trace("electron").model_dump_json(), encoding="utf-8")
    accepted = subprocess.run(
        [sys.executable, "-m", "benchmarks.desktop.acceptance", str(trace_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert accepted.stdout.strip() == "PASS: electron completed BTN-37-desktop-v1"
