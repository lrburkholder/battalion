"""BTN-38 disposable Tauri spike contract tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from benchmarks.desktop import BenchmarkTrace, validate_trace
from benchmarks.desktop.tauri.prepare import prepare


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SPIKE_ROOT = REPOSITORY_ROOT / "benchmarks" / "desktop" / "tauri"


def test_prepare_uses_shared_bundle_unchanged(tmp_path):
    fixture_path, scenario_path, measurement_path = prepare(tmp_path)

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    measurements = json.loads(measurement_path.read_text(encoding="utf-8"))

    assert fixture["fixture_id"] == "BTN-37-desktop-v1"
    assert fixture["provider_mode"] == "disabled"
    assert len(scenario) == 12
    assert len(measurements) == 9


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the Tauri renderer test")
def test_tauri_adapter_completes_shared_acceptance_contract(tmp_path):
    input_path = tmp_path / "input"
    trace_path = tmp_path / "trace.json"
    prepare(input_path)
    completed = subprocess.run(
        [
            "node",
            str(SPIKE_ROOT / "tests" / "run-contract.mjs"),
            str(input_path),
            str(trace_path),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    trace = BenchmarkTrace.model_validate_json(trace_path.read_text(encoding="utf-8"))

    validate_trace(trace)
    assert completed.stdout.strip() == "PASS: Tauri adapter completed 12 shared steps"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for Tauri adapter tests")
def test_tauri_adapter_failure_diagnostics_and_missed_event_recovery(tmp_path):
    input_path = tmp_path / "input"
    prepare(input_path)
    completed = subprocess.run(
        ["node", str(SPIKE_ROOT / "tests" / "run-failures.mjs"), str(input_path)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == (
        "PASS: Tauri adapter recovered durable state and diagnosed malformed input"
    )


def test_tauri_renderer_has_no_privileged_plugin_or_remote_network_access():
    cargo = (SPIKE_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    capability = json.loads(
        (SPIKE_ROOT / "src-tauri" / "capabilities" / "main.json").read_text(encoding="utf-8")
    )
    config = json.loads(
        (SPIKE_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )

    assert "tauri-plugin" not in cargo
    assert capability["permissions"] == ["core:default"]
    assert config["app"]["withGlobalTauri"] is True
    assert "connect-src 'self'" in config["app"]["security"]["csp"]
    assert "http:" not in config["app"]["security"]["csp"]
    assert "https:" not in config["app"]["security"]["csp"]


def test_tauri_windows_resource_icon_is_present():
    source = SPIKE_ROOT / "src-tauri" / "app-icon.png"
    windows_icon = SPIKE_ROOT / "src-tauri" / "icons" / "icon.ico"

    assert source.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert windows_icon.read_bytes().startswith(b"\x00\x00\x01\x00")


def test_tauri_measurement_hook_has_no_renderer_selected_path_or_extra_capability():
    rust = (SPIKE_ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    renderer = (SPIKE_ROOT / "ui" / "app.js").read_text(encoding="utf-8")

    assert '--benchmark-ready-file=' in rust
    assert '--benchmark-permission-probe' in rust
    assert "std::fs::write(ready_file, marker)" in rust
    assert 'code: "benchmark-ready-write-failed"' in rust
    assert 'code: "permission-probe-failed"' in rust
    assert 'invoke("benchmark_complete", { probes })' in renderer
    assert 'invoke("plugin:fs|read_text_file"' in renderer
    assert 'invoke("plugin:shell|execute"' in renderer
    assert 'fetch("https://example.invalid/btn-38-permission-probe")' in renderer
    assert "ready_file" not in renderer


def test_tauri_evidence_covers_shared_measurement_template():
    evidence = json.loads(
        (SPIKE_ROOT / "evidence" / "measurements.json").read_text(encoding="utf-8")
    )
    categories = [measurement["category"] for measurement in evidence["measurements"]]

    assert categories == [
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
    resource = evidence["measurements"][2]["observations"]
    assert len(resource["cold_start_window_ready_ms"]) == 5
    assert len(resource["idle_samples"]) == 5
    assert len(resource["scenario_samples"]) == 5
    assert evidence["measurements"][5]["observations"]["worker_crash"].startswith(
        "unsupported:"
    )
