"""Bounded source snapshots and exact scoped-write reconciliation."""

import hashlib
import os
import subprocess

import pytest
from pydantic import ValidationError

from battalion.artifact_target_sources import current_project_source_revision
from battalion.execution import ExecutionCapture
from battalion.project_source import ProjectSourceChanged, ProjectSourceSnapshot, ProjectSourceUnavailable, revalidate_project_source
from battalion.project_source_files import capture_project_source
from battalion.scope.tool_binding import build_write_tools
from battalion.state.persistence import load_state, save_state
from support.state import make_run_state


PROJECT_ID = "bd4b6e64-25fd-408a-a747-9633a803f036"


@pytest.fixture
def git_project(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    return tmp_path


def _snapshot(root, **kwargs):
    return capture_project_source(root, project_id=PROJECT_ID, **kwargs)


def test_snapshot_tracks_git_visible_source_without_contents_or_runtime_state(git_project):
    root = git_project
    (root / "widget.py").write_text("private source body", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored/local.txt").write_text("ignored value", encoding="utf-8")
    (root / "ignored/tracked.txt").write_text("tracked despite ignore", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-f", "ignored/tracked.txt"], check=True, capture_output=True)
    (root / ".battalion").mkdir()
    (root / ".battalion/state.json").write_text("runtime data", encoding="utf-8")
    first = _snapshot(root)
    assert [item.path for item in first.files] == [".gitignore", "ignored/tracked.txt", "widget.py"]
    assert first.head_revision == "unborn"
    assert first.revision.startswith("workspace-sha256:")
    serialized = first.model_dump_json()
    assert "private source body" not in serialized
    assert "runtime data" not in serialized
    assert str(root) not in serialized
    (root / ".battalion/state.json").write_text("different runtime data", encoding="utf-8")
    os.utime(root / "widget.py", None)
    assert _snapshot(root) == first


def test_source_snapshot_persists_and_rejects_rewritten_identity(git_project):
    source = _snapshot(git_project)
    state = make_run_state(schema_version="1.2", project_id=PROJECT_ID, project_source_snapshot=source)
    path = git_project / ".battalion/state.json"
    save_state(state, path)
    assert load_state(path).project_source_snapshot == source
    with pytest.raises(ValidationError, match="does not match"):
        ProjectSourceSnapshot.model_validate({**source.model_dump(), "head_revision": "a" * 40})
    with pytest.raises(ValidationError, match="frozen"):
        source.head_revision = "b" * 40


@pytest.mark.parametrize("change", ["create", "modify", "delete"])
def test_unrelated_source_changes_fail_even_inside_driver_scope(git_project, change):
    file = git_project / "widget.py"
    file.write_text("original", encoding="utf-8")
    baseline = _snapshot(git_project)
    if change == "create":
        (git_project / "another.py").write_text("new", encoding="utf-8")
    elif change == "modify":
        file.write_text("edited", encoding="utf-8")
    else:
        file.unlink()
    with pytest.raises(ProjectSourceChanged, match="without matching scoped-write evidence"):
        revalidate_project_source(baseline, _snapshot(git_project), verified_write_digests={})


@pytest.mark.parametrize("writer", ["scoped", "external", "overwritten-after-scoped"])
def test_only_actual_unchanged_scoped_writes_explain_source_changes(git_project, writer):
    baseline = _snapshot(git_project)
    state = make_run_state(
        schema_version="1.2", project_id=PROJECT_ID, project_source_snapshot=baseline,
        write_scope={"driver": ["src/"]},
    )
    capture = ExecutionCapture.start(state, "driver_red", "test-model", git_project)
    if writer != "external":
        build_write_tools("driver", state.write_scope, git_project)["src/"].write("widget.py", "scoped content")
    if writer != "scoped":
        (git_project / "src").mkdir(exist_ok=True)
        (git_project / "src/widget.py").write_text("outside change", encoding="utf-8")
    completed = capture.finish(state, state)
    attempt = completed.execution_record.node_executions[-1]
    assert len(attempt.artifact_provenance) == 1
    observed = _snapshot(git_project)
    if writer == "scoped":
        assert len(attempt.verified_scoped_writes) == 1
        assert attempt.verified_scoped_writes[0].sha256 == hashlib.sha256(b"scoped content").hexdigest()
        assert current_project_source_revision(completed, observed) == baseline.revision
    else:
        assert attempt.verified_scoped_writes == ()
        with pytest.raises(ProjectSourceChanged):
            current_project_source_revision(completed, observed)


@pytest.mark.parametrize("change", ["delete-output", "revert-output"])
def test_removing_or_reverting_a_verified_output_is_not_unchanged_source(git_project, change):
    path = git_project / "widget.py"
    if change == "revert-output":
        path.write_text("old", encoding="utf-8")
    baseline = _snapshot(git_project)
    path.write_text("new", encoding="utf-8")
    writes = {"widget.py": hashlib.sha256(b"new").hexdigest()}
    assert revalidate_project_source(baseline, _snapshot(git_project), verified_write_digests=writes) == baseline.revision
    if change == "delete-output":
        path.unlink()
    else:
        path.write_text("old", encoding="utf-8")
    with pytest.raises(ProjectSourceChanged, match="missing or changed"):
        revalidate_project_source(baseline, _snapshot(git_project), verified_write_digests=writes)


def test_head_change_requires_clarification_even_without_content_changes(git_project):
    baseline = _snapshot(git_project)
    for args in (
        ["add", ".gitignore"],
        ["-c", "user.name=Source Test", "-c", "user.email=source@example.invalid", "commit", "-m", "initial source"],
    ):
        subprocess.run(["git", "-C", str(git_project), *args], check=True, capture_output=True)
    with pytest.raises(ProjectSourceChanged, match="HEAD"):
        revalidate_project_source(baseline, _snapshot(git_project), verified_write_digests={})


def test_ignore_changes_cannot_hide_a_baseline_file(git_project):
    path = git_project / "widget.py"
    path.write_text("old", encoding="utf-8")
    baseline = _snapshot(git_project)
    (git_project / ".git/info/exclude").write_text("widget.py\n", encoding="utf-8")
    path.write_text("edited but ignored", encoding="utf-8")
    observed = _snapshot(git_project, include_paths=tuple(item.path for item in baseline.files))
    with pytest.raises(ProjectSourceChanged):
        revalidate_project_source(baseline, observed, verified_write_digests={})


def test_non_git_project_has_no_fabricated_source_revision(tmp_path):
    with pytest.raises(ProjectSourceUnavailable):
        _snapshot(tmp_path)


def test_source_limits_fail_closed_instead_of_hashing_a_prefix(git_project, monkeypatch):
    # Limit injection exercises the bounded-read seam without allocating large fixtures.
    monkeypatch.setattr("battalion.project_source_files.MAX_SOURCE_FILE_BYTES", 4)
    with pytest.raises(ProjectSourceUnavailable, match="exceed"):
        _snapshot(git_project)


def test_directory_alias_cannot_escape_the_source_project(git_project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-source")
    (outside / "external.py").write_text("external", encoding="utf-8")
    link = git_project / "alias"
    if os.name == "nt":
        link_arg = str(link).replace("'", "''")
        target_arg = str(outside).replace("'", "''")
        subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"New-Item -ItemType Junction -Path '{link_arg}' -Target '{target_arg}' -ErrorAction Stop",
        ], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectSourceUnavailable):
        _snapshot(git_project, include_paths=("alias/external.py",))
