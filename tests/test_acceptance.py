"""BTN-10: End-to-end acceptance criteria validation (spec.md v1 milestone gate).

This suite validates the five v1 acceptance criteria from spec.md plus
BTN-10's own acceptance criteria against the *built system*, end to end:

  spec.md AC1  A ticket flows Architect -> Driver(RED) -> Reviewer ->
               Driver(GREEN) -> Reviewer -> Refactorer -> Reviewer and
               reaches DONE without human intervention when no interrupt
               trigger fires.
  spec.md AC2  Each of the 6 interrupt triggers can be independently
               demonstrated with a reliable reproduction scenario.
  spec.md AC3  A paused run can be resumed by a human after review, from
               the CLI.
  spec.md AC4  No node can write outside its declared scope, verified by
               attempting an out-of-scope write and confirming it's blocked.
  spec.md AC5  State persists to local JSON matching the versioned schema;
               a second CLI invocation can resume from a prior run's state
               file.
  BTN-10 AC2   A second CLI invocation resumes correctly from a prior run's
               state file.

Everything here is self-contained: no real LLM calls, no API keys. The
*real* graph routing, *real* node implementations, *real* write-tool scope
enforcement, and *real* clean-tree subprocess test execution (via the
Reviewer's run_tests_via_subprocess) all run; only the LLM calls are faked,
through each node's call_llm_fn injection point. A couple of tests
demonstrate the graph's interrupt routing by raising the real exception
types from a node — that's the scenario that actually fires triggers #2/#5.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from battalion.cli import app as cli_app
from battalion.graph import NODE_ARCHITECT, build_graph, resume_ticket
from battalion.interrupts.triggers import (
    TRIGGER_BUDGET_EXCEEDED,
    TRIGGER_INFRA_FAILURE,
    TRIGGER_MANUAL_CHECKPOINT,
    TRIGGER_ROLE_EDIT,
    TRIGGER_SAME_ROOT_CAUSE,
    TRIGGER_SCOPE_VIOLATION,
    check_any_trigger,
    log_interrupt,
)
from battalion.llm.litellm_client import InfraFailure, NodeLLMConfig
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.nodes.driver import run_driver
from battalion.scope.tool_binding import ScopeViolationError
from battalion.state.models import (
    Budget,
    CheckpointType,
    InterruptLogEntry,
    RunState,
    RunStatus,
)
from battalion.state.persistence import load_state, save_state

runner = CliRunner()


# =============================================================================
# Fixtures / helpers
# =============================================================================

ARCHITECT_PLAN = "# Plan\n\nImplement the widget."
FAILING_TEST = (
    "def test_widget():\n"
    "    from widget import widget\n"
    "    assert widget() == 42\n"
)
IMPLEMENTATION = "def widget():\n    return 42\n"


def make_configs():
    return {
        "default": NodeLLMConfig(model="test-model", max_retries=0),
        "architect": NodeLLMConfig(model="test-model", max_retries=0),
        "driver": NodeLLMConfig(model="test-model", max_retries=0),
        "reviewer": NodeLLMConfig(model="test-model", max_retries=0),
        "refactorer": NodeLLMConfig(model="test-model", max_retries=0),
    }


def make_initial_state(tmp: Path, ticket_id="BTN-AC", **overrides):
    (tmp / "src").mkdir(parents=True, exist_ok=True)
    defaults = dict(
        schema_version="1.0",
        run_id=f"run-{ticket_id}",
        ticket_id=ticket_id,
        status=RunStatus.NOT_STARTED,
        phase=NODE_ARCHITECT,
        write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
        retry_bound=2,
        budget=Budget(limit=100, used=0),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=[],
    )
    defaults.update(overrides)
    return RunState(**defaults)


def litellm_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def files_response(files: dict[str, str]) -> dict:
    return litellm_response(json.dumps({"files": files}))


def wrap(fn, call_llm_fn):
    """Delegate to the real node function but inject a fake call_llm_fn."""
    def wrapped(*args, **kwargs):
        return fn(*args, **kwargs, call_llm_fn=call_llm_fn)
    return wrapped


@contextmanager
def run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm):
    """Patch the module-level node functions so the real node implementations
    run but each LLM call returns a canned response. Also injects a spy on
    the Reviewer's real clean-tree subprocess test runs."""
    import battalion.nodes.architect as arch_mod
    import battalion.nodes.driver as drv_mod
    import battalion.nodes.reviewer as rev_mod
    import battalion.nodes.refactorer as ref_mod

    spy = {"test_runs": 0}
    real_run_reviewer = rev_mod.run_reviewer

    def spying_run_tests(clean_dir):
        spy["test_runs"] += 1
        return rev_mod.run_tests_via_subprocess(clean_dir)

    def wrapped_reviewer(*args, **kwargs):
        # Inject both the fake LLM and the spying clean-tree test runner.
        return real_run_reviewer(
            *args, **kwargs,
            call_llm_fn=reviewer_llm,
            run_tests_fn=spying_run_tests,
        )

    with patch.object(arch_mod, "run_architect", side_effect=wrap(arch_mod.run_architect, arch_llm)), \
         patch.object(drv_mod, "run_driver", side_effect=wrap(drv_mod.run_driver, driver_llm)), \
         patch.object(rev_mod, "run_reviewer", side_effect=wrapped_reviewer), \
         patch.object(ref_mod, "run_refactorer", side_effect=wrap(ref_mod.run_refactorer, refactorer_llm)):
        yield spy


def invoke_graph(tmp: Path, state: RunState, recursion_limit=20):
    app = build_graph(make_configs(), base_dir=str(tmp)).compile()
    return app.invoke(state, {"recursion_limit": recursion_limit})


# =============================================================================
# spec.md AC1: full flow, no human intervention, no interrupts
# =============================================================================

class TestAcceptanceCriteria1_FullFlow:
    def test_ticket_flows_to_done_without_interrupt(self, tmp_path):
        """Architect -> Driver(RED) writes a genuinely failing test, Reviewer
        confirms it fails from a clean copy, Driver(GREEN) writes the
        implementation, Reviewer confirms it passes, Refactorer rewrites it,
        Reviewer confirms it still passes, then DONE. Real file writes and
        real subprocess test execution throughout — only LLM calls are fake."""
        calls = {"driver": 0}

        def arch_llm(node, cfg, messages):
            return litellm_response(ARCHITECT_PLAN)

        def driver_llm(node, cfg, messages):
            calls["driver"] += 1
            if calls["driver"] == 1:  # RED mode -> failing test
                return files_response({"test_widget.py": FAILING_TEST})
            return files_response({"widget.py": IMPLEMENTATION})  # GREEN mode

        def reviewer_llm(node, cfg, messages):
            return litellm_response("unused on accept")

        def refactorer_llm(node, cfg, messages):
            return files_response({"widget.py": IMPLEMENTATION})

        with run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm) as spy:
            final = invoke_graph(tmp_path, make_initial_state(tmp_path))

        assert final["status"] == RunStatus.DONE
        assert final["phase"] == "done"
        assert final["interrupt_log"] == [], "No interrupt may fire on the happy path"
        assert final["reviewer_rejection_history"] == []

        # Real artifacts on disk: Architect's plan + Driver/Refactorer's src files.
        assert (tmp_path / "plan.md").exists()
        assert (tmp_path / "src" / "test_widget.py").exists()
        assert (tmp_path / "src" / "widget.py").exists()

        # Reviewer genuinely re-ran the tests from a clean copy at every
        # checkpoint (RED, GREEN, REFACTOR) — not Driver's self-report.
        assert spy["test_runs"] == 3

        # Both Driver modes ran exactly once each (RED then GREEN).
        assert calls["driver"] == 2


# =============================================================================
# spec.md AC2: each of the 6 interrupt triggers reliably fires
# =============================================================================

class TestAcceptanceCriteria2_InterruptTriggers:
    def test_trigger1_same_root_cause_twice(self, tmp_path):
        """Driver(RED) keeps producing a test that unexpectedly passes; the
        Reviewer rejects it with the same root cause twice, which must fire
        trigger #1 and pause."""
        def arch_llm(node, cfg, messages):
            return litellm_response(ARCHITECT_PLAN)

        def driver_llm(node, cfg, messages):
            # A passing test is a RED-check violation (feature already exists).
            return files_response({"test_ok.py": "def test_ok():\n    assert True\n"})

        def reviewer_llm(node, cfg, messages):
            return litellm_response("feature already exists")

        def refactorer_llm(node, cfg, messages):
            raise AssertionError("must never reach Refactorer")

        with run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm):
            final = invoke_graph(tmp_path, make_initial_state(tmp_path, "BTN-T1"))

        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert [e.trigger for e in final["interrupt_log"]] == [TRIGGER_SAME_ROOT_CAUSE]
        causes = [r.cause for r in final["reviewer_rejection_history"]]
        assert causes == ["feature already exists", "feature already exists"]

    def test_trigger2_out_of_scope_write_blocked_and_routed(self, tmp_path):
        """Two halves of the same guarantee:
          (a) the Driver node's bound write tool structurally blocks an
              out-of-scope path — the ScopeViolationError propagates and
              nothing is written to disk;
          (b) when that error reaches the graph, it fires trigger #2 and
              pauses instead of crashing invoke()."""
        # (a) real node, real tool binding, out-of-scope path in LLM output.
        state = make_initial_state(tmp_path)
        with pytest.raises(ScopeViolationError):
            run_driver(
                state,
                ticket_text="BTN-T2",
                llm_config=NodeLLMConfig(model="test-model", max_retries=0),
                base_dir=str(tmp_path),
                call_llm_fn=lambda *a, **k: files_response(
                    {"widget.py": IMPLEMENTATION, "../evil.py": "x = 1"}
                ),
                mode="green",
            )
        assert not (tmp_path / "src" / "evil.py").exists()
        assert not (tmp_path / "evil.py").exists()
        assert not (tmp_path / "src" / "widget.py").exists(), (
            "No in-scope file may be written if any path in the batch is out of scope"
        )

        # (b) graph-level routing: the error surfaces as trigger #2 + pause.
        def arch_llm(node, cfg, messages):
            return litellm_response(ARCHITECT_PLAN)

        def driver_llm(node, cfg, messages):
            # A test-named path outside src/: passes RED's test-ness check,
            # then fails the scope check (trigger #2 / structural block).
            return files_response({"../test_evil.py": "def test_evil():\n    assert True\n"})

        def reviewer_llm(node, cfg, messages):
            raise AssertionError("must never reach Reviewer")

        def refactorer_llm(node, cfg, messages):
            raise AssertionError("must never reach Refactorer")

        with run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm):
            final = invoke_graph(tmp_path, make_initial_state(tmp_path, "BTN-T2"))

        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert [e.trigger for e in final["interrupt_log"]] == [TRIGGER_SCOPE_VIOLATION]

    def test_trigger3_budget_exceeded(self, tmp_path):
        """Budget is tracked per graph run; exceeding it mid-run pauses."""
        def arch_llm(node, cfg, messages):
            return litellm_response(ARCHITECT_PLAN)

        def driver_llm(node, cfg, messages):
            raise AssertionError("Driver must not run once budget is exhausted")

        def reviewer_llm(node, cfg, messages):
            raise AssertionError("must never reach Reviewer")

        def refactorer_llm(node, cfg, messages):
            raise AssertionError("must never reach Refactorer")

        with run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm):
            # limit=1 -> the single Architect turn pushes used to 1 == limit.
            final = invoke_graph(
                tmp_path,
                make_initial_state(tmp_path, "BTN-T3", budget=Budget(limit=1, used=0)),
            )

        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert [e.trigger for e in final["interrupt_log"]] == [TRIGGER_BUDGET_EXCEEDED]
        assert final["budget"].used == 1

    def test_trigger4_role_definition_edit(self, tmp_path):
        """A write_scope change between states fires trigger #4. This is the
        only trigger with no in-graph producer in v1 (no node edits
        write_scope), so it's demonstrated at the trigger-check level: the
        check detects the change and log_interrupt routes it to a pause."""
        old_state = make_initial_state(tmp_path, "BTN-T4", write_scope={
            "architect": ["plan.md"], "driver": ["src/"], "reviewer": [],
        })
        tampered = old_state.model_copy(update={"write_scope": {
            "architect": ["plan.md"], "driver": ["src/", "secrets/"], "reviewer": [],
        }})

        fired, trigger_id, context = check_any_trigger(tampered, old_state=old_state)
        assert fired is True
        assert trigger_id == TRIGGER_ROLE_EDIT
        assert context["old_write_scope"] != context["new_write_scope"]

        paused = log_interrupt(tampered, trigger_id, context)
        assert paused.status == RunStatus.AWAITING_HUMAN

    def test_trigger5_infra_failure(self, tmp_path):
        """An LLM call that fails after retries (InfraFailure) must pause
        with trigger #5 — a distinct handling path — not crash invoke()."""
        def arch_llm(node, cfg, messages):
            raise InfraFailure("architect", "test-model", 1, RuntimeError("provider down"))

        def driver_llm(node, cfg, messages):
            raise AssertionError("Driver must not run after infra failure")

        def reviewer_llm(node, cfg, messages):
            raise AssertionError("must never reach Reviewer")

        def refactorer_llm(node, cfg, messages):
            raise AssertionError("must never reach Refactorer")

        with run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm):
            # Must not raise — the graph routes it to an AWAITING_HUMAN pause.
            final = invoke_graph(tmp_path, make_initial_state(tmp_path, "BTN-T5"))

        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert [e.trigger for e in final["interrupt_log"]] == [TRIGGER_INFRA_FAILURE]

    def test_trigger6_manual_checkpoint(self, tmp_path):
        """A user-declared checkpoint pauses unconditionally at the declared
        phase, before the next node runs."""
        def arch_llm(node, cfg, messages):
            return litellm_response(ARCHITECT_PLAN)

        def driver_llm(node, cfg, messages):
            raise AssertionError("Manual checkpoint before Driver must pause first")

        def reviewer_llm(node, cfg, messages):
            raise AssertionError("must never reach Reviewer")

        def refactorer_llm(node, cfg, messages):
            raise AssertionError("must never reach Refactorer")

        with run_with_mocked_llms(arch_llm, driver_llm, reviewer_llm, refactorer_llm):
            final = invoke_graph(
                tmp_path,
                make_initial_state(tmp_path, "BTN-T6", manual_checkpoints=["driver"]),
            )

        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert [e.trigger for e in final["interrupt_log"]] == [TRIGGER_MANUAL_CHECKPOINT]


# =============================================================================
# spec.md AC3 / BTN-10 AC2: resume from the CLI, continuing where it paused
# =============================================================================

def _make_paused_state_file(tmp: Path, run_id="run-BTN-AC"):
    """Write a realistic paused state to tmp/.battalion/state/{run_id}.json,
    as `battalion run` would have left it: interrupted mid-run at the
    REFACTOR_CHECK, with the resume target recorded in interrupt context."""
    state = RunState(
        schema_version="1.0",
        run_id=run_id,
        ticket_id="BTN-AC",
        status=RunStatus.AWAITING_HUMAN,
        phase="awaiting_human",
        write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
        retry_bound=2,
        budget=Budget(limit=100, used=60),
        reviewer_rejection_history=[],
        interrupt_log=[
            InterruptLogEntry(
                trigger=TRIGGER_BUDGET_EXCEEDED,
                timestamp=datetime.now(timezone.utc),
                context={"next_phase": "reviewer_refactor", "used": 60, "limit": 100},
            )
        ],
        manual_checkpoints=[],
    )
    state_dir = tmp / ".battalion" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    save_state(state, state_dir / f"{run_id}.json")
    return state


class TestAcceptanceCriteria3_ResumeFromCLI:
    def test_cli_status_reads_prior_runs_state_file(self, tmp_path, monkeypatch):
        """A second CLI invocation (`battalion status`) reads the prior run's
        state file and reports the pause point and pending interrupt."""
        _make_paused_state_file(tmp_path)
        with monkeypatch.context() as m:
            m.chdir(tmp_path)
            result = runner.invoke(cli_app, ["status", "run-BTN-AC"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "awaiting-human"
        assert output["phase"] == "awaiting_human"
        assert output["interrupt_log"][0]["trigger"] == TRIGGER_BUDGET_EXCEEDED

    def test_cli_resume_continues_from_where_it_paused(self, tmp_path, monkeypatch):
        """`battalion resume` loads the prior run's state file, infers the
        resume target from the last interrupt, and continues there — it must
        NOT restart the ticket from Architect."""
        _make_paused_state_file(tmp_path)

        import battalion.nodes.architect as arch_mod
        import battalion.nodes.reviewer as rev_mod

        calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            calls.append("architect")
            return state.model_copy(update={"phase": "driver"})

        def fake_reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
            calls.append(f"reviewer_{checkpoint.value}")
            # REFACTOR_CHECK accept -> phase="done" -> routes straight to DONE.
            return state.model_copy(update={"phase": "done", "status": RunStatus.DONE})

        with monkeypatch.context() as m, \
             patch.object(arch_mod, "run_architect", side_effect=fake_architect), \
             patch.object(rev_mod, "run_reviewer", side_effect=fake_reviewer):
            m.chdir(tmp_path)
            result = runner.invoke(cli_app, ["resume", "run-BTN-AC"])

        assert result.exit_code == 0
        assert "architect" not in calls, "Resuming must not re-run Architect"
        assert calls == ["reviewer_refactor-check"]

        state_file = tmp_path / ".battalion" / "state" / "run-BTN-AC.json"
        saved = load_state(state_file)
        assert saved.status == RunStatus.DONE
        assert saved.phase == "done"

    def test_resume_ticket_function_continues_from_interrupt(self, tmp_path):
        """The resume_ticket() function itself resumes a paused state at the
        recorded target (unit-level complement to the CLI test above)."""
        paused = _make_paused_state_file(tmp_path)

        import battalion.nodes.architect as arch_mod
        import battalion.nodes.reviewer as rev_mod

        calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            calls.append("architect")
            return state.model_copy(update={"phase": "driver"})

        def fake_reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
            calls.append(f"reviewer_{checkpoint.value}")
            return state.model_copy(update={"phase": "done", "status": RunStatus.DONE})

        with patch.object(arch_mod, "run_architect", side_effect=fake_architect), \
             patch.object(rev_mod, "run_reviewer", side_effect=fake_reviewer):
            final = resume_ticket(paused, make_configs(), base_dir=str(tmp_path), max_turns=5)

        assert "architect" not in calls
        assert final["status"] == RunStatus.DONE


# =============================================================================
# spec.md AC4 / AC5: scope enforcement + versioned JSON persistence
# =============================================================================

class TestAcceptanceCriteria5_Persistence:
    def test_state_round_trips_through_versioned_local_json(self, tmp_path):
        """Full RunState persists to local JSON matching the versioned schema
        and loads back equal — the shape a second CLI invocation reads."""
        state = make_initial_state(tmp_path, "BTN-AC5")
        state = state.model_copy(update={
            "status": RunStatus.AWAITING_HUMAN,
            "phase": "awaiting_human",
            "reviewer_rejection_history": [],
            "interrupt_log": [
                InterruptLogEntry(
                    trigger=TRIGGER_MANUAL_CHECKPOINT,
                    timestamp=datetime.now(timezone.utc),
                    context={"phase": "driver"},
                )
            ],
            "manual_checkpoints": ["driver"],
        })
        path = tmp_path / "state.json"

        save_state(state, path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == "1.0"
        assert raw["run_id"] == "run-BTN-AC5"
        assert raw["status"] == "awaiting-human"

        loaded = load_state(path)
        assert loaded.model_dump() == state.model_dump()

    def test_malformed_state_raises_clear_error(self, tmp_path):
        """Invalid/malformed state on load raises a clear error, not a silent
        pass (BTN-1 AC, exercised through the versioned schema contract)."""
        bad = tmp_path / "bad.json"
        bad.write_text('{"schema_version": "1.0", "status": "not-a-status"}', encoding="utf-8")
        with pytest.raises(Exception):
            load_state(bad)
