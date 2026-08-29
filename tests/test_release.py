"""Release-boundary tests for BTN-136."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts import release


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_declared_package_version_is_pre_1_0_semver() -> None:
    assert release.package_version() == "0.1.0"


def test_release_tag_must_match_declared_version_exactly() -> None:
    assert release.validate_tag("v0.1.0", "0.1.0") == "0.1.0"

    with pytest.raises(ValueError, match="does not match"):
        release.validate_tag("v0.1.1", "0.1.0")


def test_release_metadata_records_source_identity(tmp_path: Path) -> None:
    output = tmp_path / "release-metadata.json"
    release.write_metadata(tag="v0.1.0", revision="abc123", output=output)

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "tag": "v0.1.0",
        "version": "0.1.0",
        "revision": "abc123",
        "version_source": "pyproject.toml [project].version",
    }


def test_release_workflow_is_tag_gated_and_publishes_only_after_validation() -> None:
    workflow_path = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow[True]["push"]["tags"] == ["v*"]
    assert "branches" not in workflow[True]["push"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["package-windows-desktop"]["needs"] == (
        "validate-and-package-python"
    )
    assert workflow["jobs"]["publish-github-release"]["needs"] == [
        "validate-and-package-python",
        "package-windows-desktop",
    ]
    publish = workflow["jobs"]["publish-github-release"]
    assert publish["permissions"] == {"contents": "write"}
    release_step = publish["steps"][-1]["run"]
    assert "gh release create" in release_step
    assert "--generate-notes" in release_step
    assert "twine" not in workflow_path.read_text(encoding="utf-8").lower()
    assert "sync_status.py" not in workflow_path.read_text(encoding="utf-8")
