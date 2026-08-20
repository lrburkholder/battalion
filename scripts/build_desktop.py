"""Build Battalion's standalone PySide6 desktop distribution."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_CONFIG = REPOSITORY_ROOT / "deployment" / "desktop" / "pysidedeploy.ini"
IGNORED_DISCOVERY_DIRS = ".venv,benchmarks,tests,docs,src,dist"


def build(*, dry_run: bool = False) -> None:
    """Run pyside6-deploy with a disposable machine-local config copy."""
    sibling_name = "pyside6-deploy.exe" if os.name == "nt" else "pyside6-deploy"
    sibling = Path(sys.executable).with_name(sibling_name)
    executable = str(sibling) if sibling.is_file() else shutil.which("pyside6-deploy")
    if executable is None:
        raise RuntimeError(
            "pyside6-deploy is unavailable; install Battalion's desktop extra first"
        )
    with tempfile.TemporaryDirectory(prefix="battalion-desktop-deploy-") as temporary:
        config = Path(temporary) / "pysidedeploy.ini"
        shutil.copyfile(DEPLOYMENT_CONFIG, config)
        command = [
            executable,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print the compiler command only"
    )
    args = parser.parse_args()
    build(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
