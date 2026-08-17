"""BTN-40 disposable Electron spike contract tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from benchmarks.desktop import BenchmarkTrace, validate_trace
from benchmarks.desktop.electron.prepare import prepare


ROOT = Path(__file__).resolve().parents[1]
SPIKE = ROOT / "benchmarks" / "desktop" / "electron"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for Electron adapter tests")
def test_electron_adapter_completes_shared_acceptance_contract(tmp_path):
    input_path = tmp_path / "input"
    trace_path = tmp_path / "trace.json"
    prepare(input_path)
    completed = subprocess.run(
        ["node", str(SPIKE / "tests" / "run-contract.mjs"), str(input_path), str(trace_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    trace = BenchmarkTrace.model_validate_json(trace_path.read_text(encoding="utf-8"))
    validate_trace(trace)
    assert completed.stdout.strip() == "PASS: Electron adapter completed 12 shared steps"


def test_renderer_is_sandboxed_and_bridge_is_narrow():
    main = (SPIKE / "src" / "main.cjs").read_text(encoding="utf-8")
    preload = (SPIKE / "src" / "preload.cjs").read_text(encoding="utf-8")
    html = (SPIKE / "src" / "renderer" / "index.html").read_text(encoding="utf-8")
    package = json.loads((SPIKE / "package.json").read_text(encoding="utf-8"))
    pnpm_settings = (SPIKE / "pnpm-workspace.yaml").read_text(encoding="utf-8")

    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert "setPermissionRequestHandler" in main
    assert "onBeforeRequest" in main
    assert 'app.setPath("userData", automatedProfile)' in main
    assert 'app.setPath("sessionData", automatedProfile)' in main
    assert "app.exit(3)" in main
    assert preload.count("ipcRenderer.") == 2
    assert "connect-src 'none'" in html
    assert "nodeLinker: hoisted" in pnpm_settings
    assert "blockExoticSubdeps: false" in pnpm_settings
    assert "electron: true" in pnpm_settings
    assert package["devDependencies"] == {
        "@electron-forge/cli": "7.11.2",
        "@electron-forge/maker-zip": "7.11.2",
        "electron": "43.4.0",
    }


def test_spike_has_no_provider_or_battalion_runtime_authority_imports():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            SPIKE / "src" / "main.cjs",
            SPIKE / "src" / "preload.cjs",
            SPIKE / "src" / "adapter.mjs",
            SPIKE / "src" / "renderer" / "renderer.mjs",
        ]
    )
    assert "OPENAI_API_KEY" not in source
    assert "battalion.graph" not in source
    assert "battalion.llm" not in source
