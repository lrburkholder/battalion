"""Tests for battalion.graph — LangGraph StateGraph wiring (BTN-7).

The graph wires Architect, Driver (RED/GREEN modes), Reviewer, and Refactorer
into a StateGraph with correct edges and interrupt pause points.

Flow: Architect -> Driver(RED) -> Reviewer(RED_CHECK) -> Driver(GREEN) ->
      Reviewer(GREEN_CHECK) -> Refactorer -> Reviewer(REFACTOR_CHECK) -> DONE

Acceptance criteria:
1. A ticket flows Architect -> Driver -> Reviewer end-to-end with no interrupt
2. Graph pauses at each defined interrupt point rather than proceeding silently

Structural checks live in TestGraphStructure. Routing behavior is exercised
by the regression classes below, which invoke the compiled graph with mocked
node implementations and assert on the actual call sequence and final state.
"""
from datetime import datetime, timezone

import pytest

from battalion.context import MAX_CONTEXT_CHARS, driver_context, refactorer_context
from battalion.nodes.driver import InvalidModeOutput
from battalion.nodes.driver import run_driver
from battalion.nodes.refactorer import MalformedRefactorerOutput
from battalion.graph import (
    NODE_ARCHITECT,
    NODE_DONE,
    NODE_DRIVER_GREEN,
    NODE_DRIVER_RED,
    NODE_BLOCKED,
    NODE_PAUSE,
    NODE_REFACTORER,
    NODE_REVIEWER_RED,
    NODE_REVIEWER_GREEN,
    NODE_REVIEWER_REFACTOR,
    build_graph,
    resume_ticket,
    run_ticket,
)
from battalion.state.models import (
    Budget,
    CheckpointType,
    InterruptLogEntry,
    RunState,
    RunStatus,
)
from battalion.scope.tool_binding import ScopeViolationError
from unittest.mock import patch

from conftest import (
    architect_advancing,
    driver_advancing,
    invoke_graph,
    make_llm_configs,
    make_run_state,
    refactorer_advancing,
    reviewer_accepting,
    reviewer_with_phases,
    resume_graph,
)


# =============================================================================
# Graph structure
# =============================================================================

EXPECTED_NODES = [
    NODE_ARCHITECT,
    NODE_DRIVER_RED,
    NODE_DRIVER_GREEN,
    NODE_REVIEWER_RED,
    NODE_REVIEWER_GREEN,
    NODE_REFACTORER,
    NODE_REVIEWER_REFACTOR,
    NODE_DONE,
    NODE_PAUSE,
    NODE_BLOCKED,
]


class TestGraphStructure:
    def test_graph_registers_all_terminal_and_role_nodes(self):
        """AC: all role, terminal, and pause nodes are wired."""
        node_names = list(build_graph(make_llm_configs()).nodes)
        for name in EXPECTED_NODES:
            assert name in node_names, f"Missing node: {name}"

    def test_graph_has_exactly_the_expected_nodes(self):
        assert set(build_graph(make_llm_configs()).nodes) == set(EXPECTED_NODES)

    def test_graph_compiles(self):
        app = build_graph(make_llm_configs()).compile()
        assert app is not None

    def test_node_names_match_phase_names(self):
        """Node names should correspond to phase names in the state."""
        assert NODE_ARCHITECT == "architect"
        assert NODE_DRIVER_RED == "driver_red"
        assert NODE_DRIVER_GREEN == "driver_green"
        assert NODE_REFACTORER == "refactorer"
        assert NODE_PAUSE == "awaiting_human"
        assert NODE_DONE == "done"
        assert NODE_BLOCKED == "blocked"


# =============================================================================
# Regression tests: real routing with mocked runners
#
# Structural checks alone once shipped four real bugs past 192 passing
# tests: interrupts fired during Architect/Driver/Refactorer were silently
# ignored (edges were unconditional), a rejected RED check was routed to
# Driver(GREEN) as if it had passed (accept/reject both set phase="driver"),
# every Reviewer checkpoint crashed with an InvalidUpdateError the moment it
# completed (a redundant add_edge fired alongside add_conditional_edges to a
# different target), and resume_ticket silently restarted every resumed run
# from Architect (resume_target was set on the state but app.invoke() always
# starts at the fixed entry point regardless of state contents). These tests
# invoke the compiled graph and watch what actually runs, so a routing
# regression fails loudly instead of compiling silently.
# =============================================================================


class TestInterruptsActuallyHaltExecution:
    """Regression tests for bug #1: interrupts fired outside a Reviewer node
    used to be logged but not acted on -- the graph proceeded to the next
    node regardless."""

    def test_budget_exceeded_during_architect_halts_before_driver(self, tmp_path):
        driver_calls = []

        def budget_blower(state, spec_text, llm_config, base_dir, prompts_dir=None):
            # Blow the budget on Architect's own turn.
            return state.model_copy(update={"budget": Budget(limit=1, used=999)})

        final = invoke_graph(
            make_run_state(),
            tmp_path,
            recursion_limit=5,
            architect=budget_blower,
            driver=driver_advancing(driver_calls),
        )

        assert driver_calls == [], "Driver must not run once budget is exceeded"
        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert len(final["interrupt_log"]) == 1
        assert final["interrupt_log"][0].trigger == "budget-exceeded"

    def test_budget_exceeded_during_driver_green_halts_before_reviewer_green(
        self, tmp_path
    ):
        calls = []

        def green_blower(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            if mode == "green":
                return state.model_copy(update={"budget": Budget(limit=1, used=999)})
            return state.model_copy(update={"phase": "reviewer"})

        final = invoke_graph(
            make_run_state(),
            tmp_path,
            recursion_limit=8,
            driver=green_blower,
            reviewer=reviewer_accepting(calls),
            refactorer=refactorer_advancing(calls),
        )

        assert "reviewer_green-check" not in calls, "Pause gate must fire on the GREEN edge"
        assert "refactorer" not in calls
        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert final["interrupt_log"][0].trigger == "budget-exceeded"

    def test_manual_checkpoint_before_driver_halts(self, tmp_path):
        driver_calls = []

        final = invoke_graph(
            # Manual checkpoints are matched against the generic role string
            # ("driver"/"reviewer"/"refactorer") that check_any_trigger is
            # called with, not a concrete node name like NODE_DRIVER_RED --
            # Architect's own pre-flight check for "am I about to hand off
            # to a declared checkpoint" uses NODE_TO_PHASE[NODE_ARCHITECT],
            # which is "driver".
            make_run_state(manual_checkpoints=["driver"]),
            tmp_path,
            recursion_limit=5,
            architect=architect_advancing(),
            driver=driver_advancing(driver_calls),
        )

        assert driver_calls == [], "Manual checkpoint before Driver(RED) must pause first"
        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert final["interrupt_log"][0].trigger == "manual-checkpoint"

    def test_completed_nodes_emit_durable_state_checkpoints(self, tmp_path):
        checkpoints = []

        invoke_graph(
            make_run_state(manual_checkpoints=["driver"]),
            tmp_path,
            recursion_limit=5,
            architect=architect_advancing(),
            on_state_checkpoint=checkpoints.append,
        )

        assert checkpoints
        assert checkpoints[0].execution_record.node_executions[0].role == "architect"
        assert checkpoints[-1].status == RunStatus.AWAITING_HUMAN


class TestCheckpointRoutingIsUnambiguous:
    """Regression tests for bug #2: RED_CHECK accept and reject used to both
    set phase="driver", so a rejected RED check was silently routed to
    Driver(GREEN) as if it had passed. The GREEN and REFACTOR rejection loops
    below pin the same guarantee at the other two checkpoints."""

    def test_red_check_reject_retries_driver_red_not_green(self, tmp_path):
        calls = []

        reject_everything = reviewer_with_phases({
            CheckpointType.RED_CHECK: "driver_red",
            CheckpointType.GREEN_CHECK: "driver_red",
            CheckpointType.REFACTOR_CHECK: "refactorer",
        }, record=calls)

        try:
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=5,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=reject_everything,
            )
        except Exception:
            pass  # Expected: mock always rejects, hits recursion limit eventually.

        # The bug: driver_green would appear here even though every reviewer
        # call rejected. It must not.
        assert "driver_green" not in calls
        assert calls.count("driver_red") >= 2

    def test_red_check_accept_advances_to_driver_green(self, tmp_path):
        calls = []

        accept_red_only = reviewer_with_phases({
            CheckpointType.RED_CHECK: "driver_green",
            CheckpointType.GREEN_CHECK: "driver",
            CheckpointType.REFACTOR_CHECK: "refactorer",
        }, record=calls)

        try:
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=6,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=accept_red_only,
            )
        except Exception:
            pass

        assert "driver_green" in calls

    def test_green_check_reject_retries_driver_green_not_refactorer(self, tmp_path):
        calls = []

        accept_red_reject_green = reviewer_with_phases({
            CheckpointType.RED_CHECK: "driver_green",
            CheckpointType.GREEN_CHECK: "driver_green",
            CheckpointType.REFACTOR_CHECK: "refactorer",
        }, record=calls)

        try:
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=8,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=accept_red_reject_green,
            )
        except Exception:
            pass

        assert calls.count("reviewer_red-check") == 1, "RED accepted exactly once"
        assert calls.count("reviewer_green-check") >= 2, "GREEN rejected into retry"
        assert calls.count("driver_green") >= 2
        # A GREEN rejection that skipped ahead would surface as a refactor
        # review without a second GREEN attempt.
        assert "reviewer_refactor-check" not in calls

    def test_refactor_check_reject_retries_refactorer_not_done(self, tmp_path):
        calls = []

        accept_through_green = reviewer_with_phases({
            CheckpointType.RED_CHECK: "driver_green",
            CheckpointType.GREEN_CHECK: "refactorer",
            CheckpointType.REFACTOR_CHECK: "refactorer",
        }, record=calls)

        try:
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=9,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=accept_through_green,
                refactorer=refactorer_advancing(calls),
            )
        except Exception:
            pass

        assert calls.count("reviewer_red-check") == 1
        assert calls.count("reviewer_green-check") == 1
        assert calls.count("reviewer_refactor-check") >= 2, "REFACTOR rejected into retry"
        assert calls.count("refactorer") >= 2


class TestReviewerCheckpointsDoNotCrash:
    """Regression tests for bug #3: every Reviewer node had a redundant
    add_edge alongside add_conditional_edges, which LangGraph fired in the
    same step whenever the conditional picked a different target --
    guaranteed InvalidUpdateError the moment any checkpoint completed."""

    def test_full_accept_path_completes_without_crashing(self, tmp_path):
        """Architect -> Driver(RED) -> Reviewer(accept) -> Driver(GREEN) ->
        Reviewer(accept) -> Refactorer -> Reviewer(accept) -> DONE, with
        every reviewer call accepting. Must reach DONE, not raise.

        The hermetic defaults in patched_nodes are exactly this scenario --
        no custom fakes needed."""
        # Must not raise InvalidUpdateError.
        final = invoke_graph(make_run_state(), tmp_path, recursion_limit=10)

        assert final["status"] == RunStatus.DONE
        assert final["phase"] == "done"


class TestRoleOutputFailuresPause:
    """Provider responses that violate a role contract are recoverable.

    These use the real graph scaffolding and the exact exception types the
    parsers raise, so the regression covers the two UAT failures without
    requiring a live provider.
    """

    def test_driver_mode_violation_retries_same_phase_with_durable_evidence(self, tmp_path):
        calls = []
        checkpoints = []

        def invalid_then_corrected(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append((mode, ticket_text))
            if mode == "red" and len([call for call in calls if call[0] == "red"]) == 1:
                raise InvalidModeOutput(
                    "RED mode must only produce test files",
                    offending_paths=("widget.py",),
                )
            return state.model_copy(update={"phase": "reviewer"})

        final = invoke_graph(
            make_run_state(), tmp_path, recursion_limit=10, driver=invalid_then_corrected,
            on_state_checkpoint=checkpoints.append,
        )

        completed = RunState.model_validate(final)
        assert completed.status == RunStatus.DONE
        assert [mode for mode, _ in calls].count("red") == 2
        assert "Battalion automatic correction" in calls[1][1]
        assert "widget.py" in calls[1][1]
        attempts = [
            item for item in completed.execution_record.node_executions
            if item.phase == NODE_DRIVER_RED
        ]
        assert len(attempts) == 2
        assert attempts[0].outcome == "rejected"
        assert attempts[0].attempt_disposition == "corrected"
        assert attempts[0].role_contract_violation.reason_code == "driver-mode-artifact"
        assert attempts[0].role_contract_violation.offending_paths == ["widget.py"]
        assert attempts[0].role_contract_violation.mutation_applied is False
        assert attempts[0].role_contract_violation.resulting_disposition == "retry"
        assert attempts[1].attempt_disposition == "accepted"
        assert completed.budget.used == 8
        correction_checkpoint = next(
            state for state in checkpoints
            if state.execution_record.node_executions[-1].role_contract_violation is not None
        )
        assert correction_checkpoint.phase == NODE_DRIVER_RED
        assert correction_checkpoint.resume_target == NODE_DRIVER_RED

    def test_repeated_role_contract_violation_escalates_after_one_retry(self, tmp_path):
        calls = []

        def invalid_red_response(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append((mode, ticket_text))
            raise InvalidModeOutput(
                "RED mode must only produce test files",
                offending_paths=("widget.py",),
            )

        final = RunState.model_validate(invoke_graph(
            make_run_state(), tmp_path, recursion_limit=5, driver=invalid_red_response
        ))

        assert final.status == RunStatus.AWAITING_HUMAN
        assert final.phase == NODE_PAUSE
        assert [mode for mode, _ in calls] == ["red", "red"]
        assert final.interrupt_log[-1].trigger == "infra-failure"
        assert final.interrupt_log[-1].context["next_phase"] == NODE_DRIVER_RED
        attempts = [
            item for item in final.execution_record.node_executions
            if item.phase == NODE_DRIVER_RED
        ]
        assert [item.role_contract_violation.attempt_number for item in attempts] == [1, 2]
        assert [item.role_contract_violation.resulting_disposition for item in attempts] == [
            "retry", "escalation"
        ]
        assert final.budget.used == 3

    def test_green_test_file_violation_retries_green_without_advancing(self, tmp_path):
        calls = []

        def invalid_green_then_corrected(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append((mode, ticket_text))
            if mode == "green" and len([call for call in calls if call[0] == "green"]) == 1:
                raise InvalidModeOutput(
                    "GREEN mode must not produce test files",
                    offending_paths=("tests/test_widget.py",),
                )
            return state.model_copy(update={"phase": "reviewer"})

        final = RunState.model_validate(invoke_graph(
            make_run_state(), tmp_path, recursion_limit=10, driver=invalid_green_then_corrected
        ))

        assert final.status == RunStatus.DONE
        assert [mode for mode, _ in calls].count("green") == 2
        green_attempts = [
            item for item in final.execution_record.node_executions
            if item.phase == NODE_DRIVER_GREEN
        ]
        assert len(green_attempts) == 2
        assert green_attempts[0].attempt_disposition == "corrected"
        assert green_attempts[0].role_contract_violation.offending_paths == [
            "tests/test_widget.py"
        ]
        assert green_attempts[1].attempt_disposition == "accepted"

    def test_scope_violation_is_not_downgraded_to_a_contract_correction(self, tmp_path):
        calls = []

        def scope_violation(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append(mode)
            raise ScopeViolationError("attempted out-of-scope write")

        final = RunState.model_validate(invoke_graph(
            make_run_state(), tmp_path, recursion_limit=5, driver=scope_violation
        ))

        assert final.status == RunStatus.AWAITING_HUMAN
        assert calls == ["red"]
        assert final.interrupt_log[-1].trigger == "out-of-scope-write"


class TestTypedDriverResults:
    """BTN-133 outcomes bypass neither execution evidence nor graph policy."""

    @staticmethod
    def _result_response(kind: str, reason_code: str) -> dict:
        import json

        return {"choices": [{"message": {"content": json.dumps({
            "files": {},
            "result": {
                "kind": kind,
                "reason_code": reason_code,
                "summary": "The supplied contract cannot be completed safely.",
            },
        })}}]}

    def test_blocked_driver_attempt_is_persisted_and_does_not_advance(self, tmp_path):
        calls = []

        def blocked_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append(mode)
            return run_driver(
                state, ticket_text, llm_config, base_dir=base_dir, mode=mode,
                prompts_dir=prompts_dir,
                call_llm_fn=lambda *args, **kwargs: self._result_response(
                    "blocked", "missing-context"
                ),
            )

        final = RunState.model_validate(invoke_graph(
            make_run_state(), tmp_path, recursion_limit=5,
            driver=blocked_driver,
            reviewer=reviewer_accepting(calls),
        ))

        assert final.status is RunStatus.BLOCKED
        assert final.phase == NODE_DRIVER_RED
        assert calls == ["red"]
        attempt = final.execution_record.node_executions[-1]
        assert attempt.phase == NODE_DRIVER_RED
        assert attempt.outcome == "succeeded"
        assert attempt.role_result.kind.value == "blocked"

    def test_escalated_driver_attempt_pauses_for_human_and_persists_result(self, tmp_path):
        def escalated_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            return run_driver(
                state, ticket_text, llm_config, base_dir=base_dir, mode=mode,
                prompts_dir=prompts_dir,
                call_llm_fn=lambda *args, **kwargs: self._result_response(
                    "escalated", "architectural-decision-required"
                ),
            )

        final = RunState.model_validate(invoke_graph(
            make_run_state(), tmp_path, recursion_limit=5, driver=escalated_driver
        ))

        assert final.status is RunStatus.AWAITING_HUMAN
        assert final.phase == NODE_PAUSE
        assert final.interrupt_log[-1].trigger == "role-escalation"
        attempt = final.execution_record.node_executions[-1]
        assert attempt.phase == NODE_DRIVER_RED
        assert attempt.outcome == "succeeded"
        assert attempt.role_result.kind.value == "escalated"

    def test_refactorer_non_json_pauses_and_retries_refactoring(self, tmp_path):
        def malformed_response(state, refactor_text, llm_config, base_dir, prompts_dir=None):
            raise MalformedRefactorerOutput(
                "Refactorer LLM output was not valid JSON: Expecting value"
            )

        final = invoke_graph(
            make_run_state(), tmp_path, recursion_limit=10, refactorer=malformed_response
        )

        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert final["phase"] == NODE_PAUSE
        interrupt = final["interrupt_log"][-1]
        assert interrupt.trigger == "infra-failure"
        assert interrupt.context["next_phase"] == NODE_REFACTORER
        assert "Refactorer LLM output was not valid JSON" in interrupt.context["error"]

        calls = []
        resumed = resume_graph(
            RunState.model_validate(final),
            tmp_path,
            max_turns=5,
            refactorer=refactorer_advancing(calls),
            reviewer=reviewer_accepting(calls),
        )
        assert calls == ["refactorer", "reviewer_refactor-check"]
        assert RunState.model_validate(resumed).status == RunStatus.DONE


class TestResumeActuallyResumes:
    """Regression tests for bug #4: resume_ticket set resume_target on the
    state but app.invoke() always starts at the fixed entry point
    (Architect) regardless of state contents -- every resume silently
    restarted the ticket from scratch."""

    def test_resume_does_not_rerun_architect(self, tmp_path):
        calls = []

        paused_state = make_run_state(
            status=RunStatus.AWAITING_HUMAN,
            phase="awaiting_human",
            interrupt_log=[
                InterruptLogEntry(
                    trigger="budget-exceeded",
                    timestamp=datetime.now(timezone.utc),
                    context={"next_phase": NODE_REVIEWER_REFACTOR},
                )
            ],
        )

        final = resume_graph(paused_state, tmp_path, max_turns=5,
                             architect=architect_advancing(calls),
                             reviewer=reviewer_accepting(calls))

        assert "architect" not in calls, "Resuming must not re-run Architect from scratch"
        # REFACTOR_CHECK accept -> phase="done" routes straight to NODE_DONE,
        # so this resume needs neither Driver nor Refactorer to finish clean.
        assert calls == ["reviewer_refactor-check"]
        assert final["status"] == RunStatus.DONE

    def test_resume_preserves_saved_run_configuration(self, tmp_path):
        captured = {}

        class FakeApp:
            def compile(self):
                return self

            def invoke(self, state, config):
                captured["state"] = state
                return state

        paused = make_run_state(
            run_id="saved-run",
            ticket_id="saved-ticket",
            spec="saved specification",
            status=RunStatus.AWAITING_HUMAN,
            phase="awaiting_human",
            write_scope={"architect": ["a.md"], "driver": ["pkg/"], "reviewer": []},
            retry_bound=9,
            budget_used=4,
            budget_limit=17,
            manual_checkpoints=["reviewer_green"],
            interrupt_log=[
                InterruptLogEntry(
                    trigger="manual-checkpoint",
                    timestamp=datetime.now(timezone.utc),
                    context={"next_phase": NODE_DRIVER_GREEN},
                )
            ],
        )

        with patch("battalion.graph.build_graph", return_value=FakeApp()):
            resume_ticket(paused, make_llm_configs(), base_dir=tmp_path)

        resumed = captured["state"]
        for field in (
            "run_id", "ticket_id", "spec", "write_scope", "retry_bound",
            "budget", "manual_checkpoints", "interrupt_log",
        ):
            assert getattr(resumed, field) == getattr(paused, field)


class TestExecutionContext:
    """BTN-26 role context is persisted, bounded, and assembled by the graph."""

    def test_run_ticket_preserves_caller_supplied_initial_state(self, tmp_path):
        captured = {}

        class FakeApp:
            def compile(self):
                return self

            def invoke(self, state, config):
                captured["state"] = state
                return state

        initial = make_run_state(
            run_id="custom-run-id",
            ticket_id="custom-ticket-id",
            spec="Persisted specification",
            write_scope={"architect": ["custom-plan.md"], "driver": ["pkg/"], "reviewer": []},
            retry_bound=7,
            budget_used=2,
            budget_limit=13,
            manual_checkpoints=["driver_green"],
        )

        with patch("battalion.graph.build_graph", return_value=FakeApp()):
            final = run_ticket(initial, make_llm_configs(), base_dir=tmp_path)

        assert captured["state"] == initial
        assert final == initial

    def test_run_ticket_rejects_duplicate_run_configuration(self, tmp_path):
        initial = make_run_state()

        with pytest.raises(TypeError, match="unexpected keyword argument 'ticket_id'"):
            run_ticket(
                initial,
                make_llm_configs(),
                base_dir=tmp_path,
                ticket_id="conflicting-ticket",
            )

    def test_graph_supplies_deterministic_role_specific_context(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (tmp_path / "plan.md").write_text("Approved plan content", encoding="utf-8")
        (source / "widget.py").write_text("IMPLEMENTATION_SENTINEL", encoding="utf-8")
        (source / "test_widget.py").write_text("TEST_SENTINEL", encoding="utf-8")
        captured = {}

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            captured["architect"] = spec_text
            return state.model_copy(update={"phase": "driver"})

        def fake_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            captured[f"driver_{mode}"] = ticket_text
            return state.model_copy(update={"phase": "reviewer"})

        def fake_refactorer(state, refactor_text, llm_config, base_dir, prompts_dir=None):
            captured["refactorer"] = refactor_text
            return state.model_copy(update={"phase": "reviewer"})

        initial = make_run_state(spec="SPECIFICATION_SENTINEL")
        final = invoke_graph(
            initial,
            tmp_path,
            recursion_limit=10,
            architect=fake_architect,
            driver=fake_driver,
            refactorer=fake_refactorer,
        )

        assert final["status"] == RunStatus.DONE
        assert final["spec"] == "SPECIFICATION_SENTINEL"
        assert "SPECIFICATION_SENTINEL" in captured["architect"]
        assert "Approved plan content" in captured["driver_red"]
        assert "IMPLEMENTATION_SENTINEL" in captured["driver_red"]
        assert "TEST_SENTINEL" not in captured["driver_red"]
        assert "Approved plan content" in captured["driver_green"]
        assert "TEST_SENTINEL" in captured["driver_green"]
        assert "IMPLEMENTATION_SENTINEL" not in captured["driver_green"]
        assert "IMPLEMENTATION_SENTINEL" in captured["refactorer"]
        assert "TEST_SENTINEL" in captured["refactorer"]
        assert all(len(context) <= MAX_CONTEXT_CHARS for context in captured.values())

    def test_context_file_order_is_stable_and_bounded(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (tmp_path / "plan.md").write_text("plan", encoding="utf-8")
        (source / "zeta.py").write_text("z" * (MAX_CONTEXT_CHARS * 2), encoding="utf-8")
        (source / "alpha.py").write_text("alpha", encoding="utf-8")
        state = make_run_state(spec="spec")

        first = driver_context(state, tmp_path, "red")
        second = driver_context(state, tmp_path, "red")

        assert first == second
        assert len(first) <= MAX_CONTEXT_CHARS
        assert first.index("src/alpha.py") < first.index("src/zeta.py")
        assert "[truncated]" in first

    def test_context_uses_phase_specific_layout_roots(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "battalion").mkdir()
        (tmp_path / "tests" / "test_widget.py").write_text("TEST_SENTINEL")
        (tmp_path / "battalion" / "widget.py").write_text("IMPLEMENTATION_SENTINEL")
        state = make_run_state(write_scope={
            "architect": ["plan.md"],
            "driver_red": ["tests/"],
            "driver_green": ["battalion/"],
            "refactorer": ["battalion/"],
            "reviewer": [],
        })

        red = driver_context(state, tmp_path, "red")
        green = driver_context(state, tmp_path, "green")
        refactor = refactorer_context(state, tmp_path)

        assert "IMPLEMENTATION_SENTINEL" in red
        assert "TEST_SENTINEL" not in red
        assert "TEST_SENTINEL" in green
        assert "IMPLEMENTATION_SENTINEL" not in green
        assert "TEST_SENTINEL" in refactor
        assert "IMPLEMENTATION_SENTINEL" in refactor
