"""Tests for battalion.scope.tool_binding — per-node write-scope enforcement
(BTN-2, plan.md ADR-002).

ADR-002's claim: a node is never *handed* a tool capable of writing outside
its declared scope. These tests pin down what "structural" means:
  1. A node's returned tool set has no entry for another node's paths at all
     (cross-root violation -> no matching tool exists, not a runtime check).
  2. Within a declared root, path traversal is still guarded locally (a root-
     scoped check), which is the one place a runtime check is unavoidable —
     but it's local to that root, not a central cross-node table lookup.
"""
import pytest

from pathlib import Path

from battalion.scope.tool_binding import ScopeViolationError, build_write_tools


WRITE_SCOPE = {
    "architect": ["plan.md"],
    "driver": ["src/"],
    "reviewer": [],
}


def test_architect_toolset_has_only_its_declared_file():
    tools = build_write_tools("architect", WRITE_SCOPE)
    assert set(tools.keys()) == {"plan.md"}


def test_architect_toolset_has_no_tool_for_driver_paths():
    tools = build_write_tools("architect", WRITE_SCOPE)
    assert "src/" not in tools
    # No tool object anywhere in architect's set can reach src/ — confirmed
    # by the fact there's simply no key for it, not by trying and catching.


def test_reviewer_gets_empty_toolset():
    tools = build_write_tools("reviewer", WRITE_SCOPE)
    assert tools == {}


def test_driver_can_write_within_its_declared_root(tmp_path):
    tools = build_write_tools("driver", WRITE_SCOPE, base_dir=tmp_path)
    tools["src/"].write("module.py", "print('hi')")
    assert (tmp_path / "src" / "module.py").read_text() == "print('hi')"


def test_driver_write_blocked_outside_declared_root_via_traversal(tmp_path):
    tools = build_write_tools("driver", WRITE_SCOPE, base_dir=tmp_path)
    with pytest.raises(ScopeViolationError):
        tools["src/"].write("../plan.md", "sneaky content")
    assert not (tmp_path / "plan.md").exists()


def test_architect_can_write_its_single_declared_file(tmp_path):
    tools = build_write_tools("architect", WRITE_SCOPE, base_dir=tmp_path)
    tools["plan.md"].write("plan.md", "# Plan")
    assert (tmp_path / "plan.md").read_text() == "# Plan"


def test_single_file_tool_rejects_a_different_filename(tmp_path):
    tools = build_write_tools("architect", WRITE_SCOPE, base_dir=tmp_path)
    with pytest.raises(ScopeViolationError):
        tools["plan.md"].write("not-plan.md", "sneaky")


def test_violation_triggers_on_violation_callback(tmp_path):
    violations = []
    tools = build_write_tools(
        "driver", WRITE_SCOPE, base_dir=tmp_path, on_violation=violations.append
    )
    with pytest.raises(ScopeViolationError):
        tools["src/"].write("../plan.md", "sneaky")
    assert len(violations) == 1
    assert violations[0]["node"] == "driver"


def test_driver_write_blocked_for_absolute_path(tmp_path):
    """pathlib's `/` operator discards the left operand when the right side
    is absolute (root / "/etc/passwd" == Path("/etc/passwd")) — this must
    not silently bypass the declared root."""
    before = Path("/etc/passwd").read_text()
    tools = build_write_tools("driver", WRITE_SCOPE, base_dir=tmp_path)
    with pytest.raises(ScopeViolationError):
        tools["src/"].write("/etc/passwd", "sneaky content")
    assert Path("/etc/passwd").read_text() == before


def test_unknown_node_name_gets_empty_toolset():
    tools = build_write_tools("nonexistent-node", WRITE_SCOPE)
    assert tools == {}
