"""Desktop packaging, launch, and presentation authority boundaries."""


from __future__ import annotations


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


import configparser
import subprocess
import sys
import tomllib
from pathlib import Path
from battalion.identity import load_project_identity


def test_desktop_modules_have_no_graph_or_persistence_authority():
    root = Path(__file__).resolve().parents[1] / "battalion" / "desktop"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    assert "battalion.graph" not in source
    assert "battalion.state.persistence" not in source
    assert "resume_run(" not in source
    assert "start_run(" not in source
    assert ".write_text(" not in source
    assert ".open(" not in source


def test_desktop_packaging_contract_is_reproducible():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deployment = configparser.ConfigParser()
    deployment.read(root / "deployment" / "desktop" / "pysidedeploy.ini")

    assert project["project"]["optional-dependencies"]["desktop"] == [
        "PySide6==6.10.1",
        "Nuitka==4.1.3",
    ]
    assert "langgraph>=1.2,<2.0" in project["project"]["dependencies"]
    assert project["project"]["scripts"]["battalion-desktop"] == (
        "battalion.desktop.app:main"
    )
    assert deployment["app"]["project_dir"] == "."
    assert deployment["app"]["input_file"] == "battalion/desktop/__main__.py"
    assert deployment["nuitka"]["mode"] == "standalone"
    assert "--nofollow-import-to=battalion.graph" in deployment["nuitka"]["extra_args"]
    assert "--nofollow-import-to=litellm" in deployment["nuitka"]["extra_args"]
    assert "--nofollow-import-to=langgraph" in deployment["nuitka"]["extra_args"]
    assert "--noinclude-pytest-mode=nofollow" in deployment["nuitka"]["extra_args"]
    assert (
        "--include-data-dir=battalion/desktop/assets=battalion/desktop/assets"
        in deployment["nuitka"]["extra_args"]
    )
    assert "--report=dist/desktop/desktop-compilation-report.xml" in (
        deployment["nuitka"]["extra_args"]
    )
    assert deployment["python"]["python_path"] == ""
    assert deployment["app"]["icon"] == ""
    assert project["tool"]["setuptools"]["package-data"]["battalion.desktop"] == [
        "assets/*",
        "assets/fonts/*",
    ]
    assert set(deployment["qt"]["modules"].split(",")) == {"Core", "Gui", "Widgets"}
    assert "accessiblebridge" in deployment["qt"]["plugins"]


def test_desktop_entrypoint_launches_from_an_unrelated_working_directory(tmp_path):
    load_project_identity(tmp_path, create=True)
    screenshot = tmp_path / "desktop.png"
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "battalion.desktop",
            "--project",
            str(tmp_path),
            "--screenshot",
            str(screenshot),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_importing_battalion_does_not_initialize_graph_authority(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import battalion; "
                "assert 'battalion.graph' not in sys.modules; "
                "from battalion import RunState; "
                "assert RunState.__name__ == 'RunState'; "
                "assert 'battalion.graph' not in sys.modules"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
