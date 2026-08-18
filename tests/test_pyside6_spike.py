"""BTN-39 disposable PySide6 spike contract tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QListWidget, QPlainTextEdit

from benchmarks.desktop import BenchmarkTrace, validate_trace
from benchmarks.desktop.pyside6.adapter import PySideBenchmarkAdapter, run_scenario
from benchmarks.desktop.pyside6.app import BenchmarkWindow
from benchmarks.desktop.pyside6.prepare import prepare


@pytest.fixture(scope="module")
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application


def _inputs(tmp_path: Path) -> tuple[dict, list[dict]]:
    fixture_path, scenario_path, _ = prepare(tmp_path)
    return (
        json.loads(fixture_path.read_text(encoding="utf-8")),
        json.loads(scenario_path.read_text(encoding="utf-8")),
    )


def test_pyside6_adapter_completes_shared_acceptance_contract(tmp_path):
    fixture, scenario = _inputs(tmp_path)
    trace = BenchmarkTrace.model_validate(run_scenario(fixture, scenario))

    validate_trace(trace)
    assert [entry.step_id for entry in trace.entries] == [step["step_id"] for step in scenario]


def test_adapter_rejects_unknown_fixture():
    with pytest.raises(ValueError, match="unsupported fixture"):
        PySideBenchmarkAdapter({"fixture_id": "wrong"})


def test_qt_window_renders_all_steps_and_trace_offscreen(tmp_path, qt_application):
    fixture, scenario = _inputs(tmp_path)
    window = BenchmarkWindow(fixture, scenario)
    window.show()
    qt_application.processEvents()

    steps = window.findChild(QListWidget, "steps")
    trace = window.findChild(QPlainTextEdit, "trace")
    assert steps is not None and steps.count() == 12
    assert trace is not None and '"framework": "pyside6"' in trace.toPlainText()
    assert steps.accessibleName() == "Completed shared benchmark steps"
    window.close()


def test_spike_has_no_provider_or_runtime_authority_imports():
    root = Path(__file__).resolve().parents[1] / "benchmarks" / "desktop" / "pyside6"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "adapter.py", root / "app.py")
    )
    assert "battalion.graph" not in source
    assert "battalion.llm" not in source
    assert "battalion.persistence" not in source
    assert "OPENAI_API_KEY" not in source


def test_packaging_is_pinned_and_bundles_the_shared_inputs():
    root = Path(__file__).resolve().parents[1] / "benchmarks" / "desktop" / "pyside6"
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    deployment = (root / "pysidedeploy.spec").read_text(encoding="utf-8")

    assert requirements.strip() == "PySide6==6.10.1"
    assert "packages = Nuitka==4.1.3" in deployment
    assert "mode = standalone" in deployment
    assert "--include-data-dir=benchmark-input=benchmark-input" in deployment
