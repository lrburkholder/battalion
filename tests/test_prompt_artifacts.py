"""Archive-level prompt inventory checks for Python release artifacts."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from battalion.prompts.loader import SHIPPED_PROMPT_NAMES
from scripts.verify_prompt_artifacts import verify_archive


def _members(prefix: str = "") -> dict[str, bytes]:
    return {
        f"{prefix}battalion/prompts/{name}.md": name.encode("utf-8")
        for name in SHIPPED_PROMPT_NAMES
    }


def _write_wheel(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _write_sdist(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_verifier_accepts_complete_wheel_and_sdist(tmp_path: Path) -> None:
    wheel = tmp_path / "battalion-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "battalion-0.1.0.tar.gz"
    _write_wheel(wheel, _members())
    _write_sdist(sdist, _members("battalion-0.1.0/"))

    verify_archive(wheel)
    verify_archive(sdist)


def test_verifier_rejects_an_omitted_required_prompt(tmp_path: Path) -> None:
    wheel = tmp_path / "battalion-0.1.0-py3-none-any.whl"
    members = _members()
    members.pop("battalion/prompts/architect.md")
    _write_wheel(wheel, members)

    with pytest.raises(RuntimeError, match="architect"):
        verify_archive(wheel)
