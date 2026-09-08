"""Verify that Python release archives contain every shipped role prompt."""

from __future__ import annotations

import argparse
import glob
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from battalion.prompts.loader import SHIPPED_PROMPT_NAMES


def prompt_names(member_names: list[str]) -> set[str]:
    """Extract direct ``battalion/prompts/*.md`` members from an archive."""
    names: set[str] = set()
    for member_name in member_names:
        parts = PurePosixPath(member_name).parts
        for index in range(len(parts) - 2):
            if parts[index : index + 2] != ("battalion", "prompts"):
                continue
            if index + 3 != len(parts):
                continue
            prompt = PurePosixPath(parts[-1])
            if prompt.suffix == ".md":
                names.add(prompt.stem)
    return names


def verify_archive(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            actual = prompt_names(archive.namelist())
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            actual = prompt_names(archive.getnames())
    else:
        raise ValueError(f"Unsupported Python release archive: {path}")

    required = set(SHIPPED_PROMPT_NAMES)
    if actual != required:
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        raise RuntimeError(
            f"Prompt inventory mismatch in {path}: missing={missing}, "
            f"unexpected={unexpected}"
        )


def _expand(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        paths.extend(Path(match) for match in matches)
    if not paths:
        raise FileNotFoundError("No Python release archives matched the supplied paths")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", help="Wheel/sdist paths or glob patterns")
    args = parser.parse_args()
    for archive in _expand(args.archives):
        verify_archive(archive)
        print(f"Verified shipped prompts in {archive}")


if __name__ == "__main__":
    main()
