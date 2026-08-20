"""Command-level tests for split BTN-43 desktop packaging."""

from __future__ import annotations

import configparser
import sys

from scripts import build_desktop


def test_worker_command_is_heavy_runtime_only_and_excludes_pytest():
    command = build_desktop._worker_command()

    assert command[:3] == [sys.executable, "-m", "nuitka"]
    assert "--mode=standalone" in command
    assert "--noinclude-pytest-mode=nofollow" in command
    assert "--output-filename=BattalionWorker.exe" in command
    assert str(build_desktop.WORKER_ENTRY) == command[-1]
    assert not any("pyside6" in item.lower() for item in command)


def test_build_can_select_desktop_worker_or_both(monkeypatch):
    calls = []
    monkeypatch.setattr(
        build_desktop,
        "_build_desktop",
        lambda *, dry_run: calls.append(("desktop", dry_run)),
    )
    monkeypatch.setattr(
        build_desktop,
        "_build_worker",
        lambda *, dry_run: calls.append(("worker", dry_run)),
    )

    build_desktop.build(component="desktop", dry_run=True)
    build_desktop.build(component="worker", dry_run=False)
    build_desktop.build(component="all", dry_run=True)

    assert calls == [
        ("desktop", True),
        ("worker", False),
        ("desktop", True),
        ("worker", True),
    ]


def test_disposable_deploy_config_replaces_machine_specific_paths(tmp_path):
    destination = tmp_path / "pysidedeploy.ini"
    build_desktop._write_local_config(destination)
    parser = configparser.ConfigParser()
    parser.read(destination)

    assert parser["python"]["python_path"] == sys.executable
    assert parser["app"]["project_dir"] == str(build_desktop.REPOSITORY_ROOT)
    assert parser["app"]["exec_directory"] == str(
        build_desktop.REPOSITORY_ROOT / "dist" / "desktop"
    )
    assert parser["app"]["icon"] == str(build_desktop.APPLICATION_ICON)
