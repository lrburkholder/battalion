"""Build Battalion's standalone PySide6 desktop distribution."""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_CONFIG = REPOSITORY_ROOT / "deployment" / "desktop" / "pysidedeploy.ini"
APPLICATION_ICON = REPOSITORY_ROOT / "battalion" / "desktop" / "assets" / "favicon.ico"
IGNORED_DISCOVERY_DIRS = ".venv,benchmarks,tests,docs,src,dist"
WORKER_ENTRY = REPOSITORY_ROOT / "battalion" / "worker_entry.py"
WORKER_OUTPUT_DIR = REPOSITORY_ROOT / "dist" / "desktop" / "worker"
WORKER_REPORT = REPOSITORY_ROOT / "dist" / "desktop" / "worker-compilation-report.xml"


def _desktop_deployer() -> str:
    sibling_name = "pyside6-deploy.exe" if os.name == "nt" else "pyside6-deploy"
    sibling = Path(sys.executable).with_name(sibling_name)
    executable = str(sibling) if sibling.is_file() else shutil.which("pyside6-deploy")
    if executable is None:
        raise RuntimeError(
            "pyside6-deploy is unavailable; install Battalion's desktop extra first"
        )
    return executable


def _write_local_config(destination: Path) -> None:
    """Replace workstation paths in the checked-in template at build time."""
    parser = configparser.ConfigParser()
    parser.read(DEPLOYMENT_CONFIG)
    parser["app"]["project_dir"] = str(REPOSITORY_ROOT)
    parser["app"]["exec_directory"] = str(REPOSITORY_ROOT / "dist" / "desktop")
    parser["python"]["python_path"] = sys.executable
    parser["app"]["icon"] = str(APPLICATION_ICON)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        parser.write(stream)


def _build_desktop(*, dry_run: bool) -> None:
    """Build only the lightweight Qt presentation executable."""
    with tempfile.TemporaryDirectory(prefix="battalion-desktop-deploy-") as temporary:
        config = Path(temporary) / "pysidedeploy.ini"
        _write_local_config(config)
        command = [
            _desktop_deployer(),
            "-c",
            str(config),
            "--extra-ignore-dirs",
            IGNORED_DISCOVERY_DIRS,
        ]
        if dry_run:
            command.append("--dry-run")
        else:
            command.append("--force")
        subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)


def _worker_command() -> list[str]:
    """Return the reproducible standalone worker build command."""
    return [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=standalone",
        "--follow-imports",
        "--assume-yes-for-downloads",
        "--noinclude-pytest-mode=nofollow",
        "--include-data-files=battalion/prompts/*.md=battalion/prompts/",
        f"--report={WORKER_REPORT}",
        f"--output-dir={WORKER_OUTPUT_DIR}",
        "--output-filename=BattalionWorker.exe",
        str(WORKER_ENTRY),
    ]


def _build_worker(*, dry_run: bool) -> None:
    """Build the graph/provider worker independently from the Qt client."""
    command = _worker_command()
    if dry_run:
        print(subprocess.list2cmdline(command))
        return
    if importlib.util.find_spec("nuitka") is None:
        raise RuntimeError("Nuitka is unavailable; install Nuitka==4.1.3 first")
    WORKER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)


def build(*, component: str = "all", dry_run: bool = False) -> None:
    """Build the desktop client, worker runtime, or both split components."""
    if component in {"desktop", "all"}:
        _build_desktop(dry_run=dry_run)
    if component in {"worker", "all"}:
        _build_worker(dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print the compiler command only"
    )
    parser.add_argument(
        "--component",
        choices=("desktop", "worker", "all"),
        default="all",
        help="build the lightweight desktop, heavy worker, or both",
    )
    args = parser.parse_args()
    build(component=args.component, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
