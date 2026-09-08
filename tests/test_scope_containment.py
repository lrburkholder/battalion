"""BTN-166: declarations cannot grant authority outside the admitted project."""

import os
import subprocess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from battalion.application import (
    InvalidWriteScope, ResumeRun, StartRun, StartWorker, create_initial_state,
    resume_run, start_run, start_worker,
)
from battalion.config import BattalionConfig, load_config, save_config
from battalion.cli import app
from battalion.context import driver_context
from battalion.execution import ExecutionCapture
from battalion.graph import build_graph
from battalion.llm.litellm_client import NodeLLMConfig
from battalion.nodes.architect import run_architect
from battalion.nodes.driver import run_driver
from battalion.nodes.refactorer import run_refactorer
from battalion.scope.tool_binding import (
    ScopeViolationError, WriteScopeMisconfigured, build_write_tools,
    normalize_scope_root, resolve_scoped_batch,
)
from battalion.state.models import RunState
from battalion.state.persistence import save_state
from support.state import make_llm_configs, make_run_state


@pytest.mark.parametrize("entry", [
    "../outside/", "../outside.py", "src/../../outside/",
    "src\\..\\..\\outside.py", "src/..\\../outside/",
    "src/../safe/", "/outside/", "/outside.py", "\\outside\\",
    "C:outside/", "C:outside.py", "Z:/outside/", "Z:\\outside.py",
    "//server/share/outside/", "\\\\server\\share\\outside.py",
    "\\\\?\\C:\\outside/", "\\\\.\\C:\\outside.py",
    "", ".", "./", ".\\", "././", "src/../", "src\\..\\",
    "src/.. /outside/", "src/code.py:stream", "NUL", "src\x00/",
    "NUL/nested/", "pkg/AUX.py", "pkg/COM1/", "pkg/trailing./", "pkg/file*/",
])
def test_invalid_declarations_never_expose_tools(tmp_path, entry):
    with pytest.raises(WriteScopeMisconfigured):
        build_write_tools("driver_green", {"driver_green": ["safe/", entry]}, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("single_file", [False, True])
@pytest.mark.parametrize("inside", [False, True])
def test_absolute_declarations_are_not_project_relative(tmp_path, single_file, inside):
    project = tmp_path / "project"
    target = (project if inside else tmp_path) / "target"
    entry = str(target / "code.py") if single_file else str(target) + "/"
    with pytest.raises(WriteScopeMisconfigured):
        build_write_tools("driver", {"driver": [entry]}, project)


@pytest.mark.parametrize("entry", ["pkg/nested/", "pkg\\nested\\", "pkg/nested\\"])
def test_nested_directory_roots_and_qualified_batches(tmp_path, entry):
    tools = build_write_tools("driver", {"driver": [entry, "other/"]}, tmp_path)
    for tool, path in resolve_scoped_batch(tools, ["pkg/nested/code.py", "other/code.py"]):
        tool.write(path, "safe")
    assert (tmp_path / "pkg/nested/code.py").read_text() == "safe"
    assert (tmp_path / "other/code.py").read_text() == "safe"
    assert tools[entry].resolve("deeper\\code.py") == tmp_path / "pkg/nested/deeper/code.py"


@pytest.mark.parametrize("entry", ["docs/nested/plan.md", "docs\\nested/plan.md"])
def test_nested_single_file_roots(tmp_path, entry):
    tool = build_write_tools("architect", {"architect": [entry]}, tmp_path)[entry]
    tool.write("plan.md", "plan")
    assert (tmp_path / "docs/nested/plan.md").read_text() == "plan"
    with pytest.raises(ScopeViolationError):
        tool.write("other.md", "no")


def test_invalid_inactive_scope_cannot_be_hidden_by_valid_legacy_scope(tmp_path):
    scope = {"architect": ["plan.md"], "driver": ["src/"], "driver_green": ["../outside/"]}
    with pytest.raises(WriteScopeMisconfigured):
        build_write_tools("architect", scope, tmp_path)


def symlink_or_skip(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


@pytest.mark.parametrize("entry", ["link/", "link/code.py", "link/missing/nested/", "plan.md"])
def test_symlink_escape_at_binding(tmp_path, entry):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "code.py").write_text("unchanged")
    if entry == "plan.md":
        symlink_or_skip(project / entry, outside / "code.py")
    else:
        symlink_or_skip(project / "link", outside, directory=True)
    with pytest.raises(WriteScopeMisconfigured):
        build_write_tools("driver", {"driver": [entry]}, project)
    assert (outside / "code.py").read_text() == "unchanged"


def test_link_back_to_project_root_is_not_a_nested_grant(tmp_path):
    symlink_or_skip(tmp_path / "alias", tmp_path, directory=True)
    with pytest.raises(WriteScopeMisconfigured):
        normalize_scope_root("alias/", tmp_path)


def test_internal_link_and_linked_project_base_are_supported(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "actual").mkdir()
    symlink_or_skip(tmp_path / "base", project, directory=True)
    symlink_or_skip(project / "alias", project / "actual", directory=True)
    symlink_or_skip(project / "plan.md", project / "actual/renamed.md")
    scope = {"architect": ["plan.md"], "driver": ["alias/"]}
    build_write_tools("architect", scope, tmp_path / "base")["plan.md"].write("plan.md", "plan")
    build_write_tools("driver", scope, tmp_path / "base")["alias/"].write("code.py", "code")
    assert (project / "actual/renamed.md").read_text() == "plan"
    assert (project / "actual/code.py").read_text() == "code"


@pytest.mark.parametrize("entry", ["src/", "plan.md", "src/nested/"])
def test_retargeting_bound_root_is_an_audited_scope_violation(tmp_path, entry):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    violations = []
    tool = build_write_tools("driver", {"driver": [entry]}, project, violations.append)[entry]
    if entry == "plan.md":
        symlink_or_skip(project / "plan.md", outside / "code.py")
        name = "plan.md"
    else:
        symlink_or_skip(project / "src", outside, directory=True)
        name = "code.py"
    with pytest.raises(ScopeViolationError):
        tool.write(name, "no")
    assert len(violations) == 1
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("entry", ["loop/", "loop/nested/", "loop/plan.md"])
def test_symlink_loop_has_typed_failure(tmp_path, entry):
    symlink_or_skip(tmp_path / "loop", tmp_path / "loop", directory=True)
    with pytest.raises(WriteScopeMisconfigured):
        normalize_scope_root(entry, tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
@pytest.mark.parametrize("entry", ["link/", "link/code.py"])
def test_windows_junction_escape(tmp_path, entry):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = str(project / "link").replace("'", "''")
    target = str(outside).replace("'", "''")
    subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' -ErrorAction Stop",
    ], check=True, capture_output=True)
    with pytest.raises(WriteScopeMisconfigured):
        normalize_scope_root(entry, project)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
@pytest.mark.parametrize("entry", ["link/", "link/plan.md"])
def test_windows_junction_created_after_binding_is_blocked(tmp_path, entry):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    violations = []
    tool = build_write_tools("driver", {"driver": [entry]}, project, violations.append)[entry]
    link = str(project / "link").replace("'", "''")
    target = str(outside).replace("'", "''")
    subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' -ErrorAction Stop",
    ], check=True, capture_output=True)
    with pytest.raises(ScopeViolationError):
        tool.write("plan.md", "no")
    assert len(violations) == 1
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("error", [OSError("inaccessible"), ValueError("invalid path"), RuntimeError("loop")])
def test_resolution_errors_are_typed_configuration_failures(tmp_path, monkeypatch, error):
    def cannot_resolve(*args, **kwargs):
        raise error

    monkeypatch.setattr(type(tmp_path), "resolve", cannot_resolve)
    with pytest.raises(WriteScopeMisconfigured, match="Cannot resolve write scope"):
        normalize_scope_root("src/", tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows case/drive semantics")
def test_windows_base_case_differences_do_not_reject_contained_roots(tmp_path):
    tool = build_write_tools("driver", {"driver": ["SRC/"]}, str(tmp_path).swapcase())["SRC/"]
    tool.write("code.py", "safe")
    assert (tmp_path / "src/code.py").read_text() == "safe"


@pytest.mark.parametrize("entry", ["pkg\\nested/", "../outside/", "C:outside/"])
def test_configuration_and_state_round_trips_preserve_authority(tmp_path, entry):
    scope = {"architect": ["plan.md"], "driver": ["legacy/"], "driver_green": [entry]}
    path = tmp_path / "config.yaml"
    save_config({}, path, existing={"base_dir": str(tmp_path), "write_scope": scope})
    config = load_config(path)
    state = RunState.model_validate_json(make_run_state(write_scope=config.write_scope).model_dump_json())
    assert state.write_scope == scope
    if entry == "pkg\\nested/":
        tools = build_write_tools("driver_green", state.write_scope, config.base_dir)
        tools[entry].write("code.py", "safe")
        assert (tmp_path / "pkg/nested/code.py").read_text() == "safe"
    else:
        with pytest.raises(WriteScopeMisconfigured):
            build_write_tools("driver_green", state.write_scope, config.base_dir)
        assert not (tmp_path / "legacy").exists()


def test_mixed_separator_roots_keep_context_and_artifact_evidence(tmp_path):
    entry = "pkg\\nested/"
    state = make_run_state(write_scope={"driver_green": [entry]})
    tool = build_write_tools("driver_green", state.write_scope, tmp_path)[entry]
    tool.write("code.py", "VALUE = 1")
    context = driver_context(state, tmp_path, "red")
    capture = ExecutionCapture.start(state, "driver_green", "test-model", tmp_path)
    tool.write("code.py", "VALUE = 2")
    completed = capture.finish(state, state)
    assert "VALUE = 1" in context
    assert "pkg/nested/code.py" in capture.before_files
    execution = completed.execution_record.node_executions[-1]
    assert [item.path for item in execution.artifact_provenance] == ["pkg/nested/code.py"]


@pytest.mark.parametrize("role", ["architect", "red", "green", "refactorer"])
def test_invalid_role_scope_is_rejected_before_provider_call(tmp_path, role):
    scope_key = f"driver_{role}" if role in {"red", "green"} else role
    state = make_run_state(write_scope={"driver": ["safe/"], scope_key: ["../outside/"]})
    call = Mock(side_effect=AssertionError("configuration must fail before provider call"))
    kwargs = dict(state=state, llm_config=NodeLLMConfig(model="test"), base_dir=tmp_path, call_llm_fn=call)
    with pytest.raises(WriteScopeMisconfigured):
        if role == "architect":
            run_architect(spec_text="plan", **kwargs)
        elif role == "refactorer":
            run_refactorer(refactor_text="refactor", **kwargs)
        else:
            run_driver(ticket_text="implement", mode=role, **kwargs)
    call.assert_not_called()


def test_graph_rejects_invalid_scopes_before_attempt_or_snapshot(tmp_path):
    state = make_run_state(write_scope={"architect": ["plan.md"], "driver_green": ["../outside/"]})
    checkpoint = Mock()
    graph = build_graph(make_llm_configs(), base_dir=str(tmp_path), on_state_checkpoint=checkpoint).compile()
    with pytest.raises(WriteScopeMisconfigured):
        graph.invoke(state)
    checkpoint.assert_not_called()
    assert state.budget.used == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["create", "start", "resume", "worker-start", "worker-resume"])
def test_application_reports_configuration_error_without_mutation(tmp_path, operation):
    config = BattalionConfig(base_dir=str(tmp_path), write_scope={"driver": ["../outside/"]})
    state = make_run_state(write_scope=config.write_scope)
    path = tmp_path / f"{state.run_id}.json"
    if "resume" in operation:
        save_state(state, path)
    before = path.read_bytes() if path.exists() else None
    execute = Mock(side_effect=AssertionError("invalid scope cannot execute"))
    start = StartRun(initial_state=state, config=config)
    resume = ResumeRun(run_id=state.run_id, config=config)
    with pytest.raises(InvalidWriteScope):
        if operation == "create":
            create_initial_state("BTN-166", "containment", config)
        elif operation == "start":
            start_run(start, state_dir=tmp_path, _execute=execute)
        elif operation == "resume":
            # Saved authority wins over a subsequently edited YAML config.
            resume_run(ResumeRun(run_id=state.run_id, config=BattalionConfig(base_dir=str(tmp_path))),
                       state_dir=tmp_path, _execute=execute)
        else:
            start_worker(StartWorker(command=resume if "resume" in operation else start),
                         state_dir=tmp_path, worker_dir=tmp_path / "workers")
    execute.assert_not_called()
    assert (path.read_bytes() if path.exists() else None) == before
    assert not (tmp_path / ".battalion").exists()
    assert not (tmp_path / "workers").exists()


def test_cli_renders_scope_configuration_error_without_starting_run(tmp_path):
    config = tmp_path / "config.yaml"
    save_config({}, config, existing={"write_scope": {"driver": ["../outside/"]}})
    result = CliRunner().invoke(app, [
        "run", "BTN-166", "--spec", "scope test", "--config", str(config),
        "--base-dir", str(tmp_path),
    ])
    assert result.exit_code == 1
    assert "Invalid write scope" in result.output
    assert "Starting run" not in result.output
    assert not (tmp_path / ".battalion").exists()
