"""Validate the tag/version release boundary and write release provenance."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def package_version(repository_root: Path = REPOSITORY_ROOT) -> str:
    """Return the single declared application/package version."""
    project = tomllib.loads(
        (repository_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    version = project["project"]["version"]
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ValueError("pyproject.toml project.version must be a SemVer version")
    return version


def validate_tag(tag: str, version: str | None = None) -> str:
    """Require the maintainer's tag to name exactly the declared version."""
    version = package_version() if version is None else version
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ValueError(
            f"release tag {tag!r} does not match declared version {version!r}; "
            f"expected {expected_tag!r}"
        )
    return version


def write_metadata(*, tag: str, revision: str, output: Path) -> None:
    """Write the minimal provenance that accompanies every release artifact set."""
    version = validate_tag(tag)
    if not revision:
        raise ValueError("release revision must not be empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tag": tag,
                "version": version,
                "revision": revision,
                "version_source": "pyproject.toml [project].version",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate_parser = commands.add_parser("validate-tag")
    validate_parser.add_argument("--tag", required=True)

    metadata_parser = commands.add_parser("write-metadata")
    metadata_parser.add_argument("--tag", required=True)
    metadata_parser.add_argument("--revision", required=True)
    metadata_parser.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "validate-tag":
        validate_tag(args.tag)
    else:
        write_metadata(tag=args.tag, revision=args.revision, output=args.output)


if __name__ == "__main__":
    main()
