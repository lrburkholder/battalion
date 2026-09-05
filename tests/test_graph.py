"""Graph structure, required checkpoint routing, and interrupt regressions."""


import pytest
from langgraph.errors import GraphRecursionError
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
)
from battalion.state.models import Budget, CheckpointType, RunStatus
from support.state import make_llm_configs, make_run_state
from support.graph import (
    architect_advancing,
    driver_advancing,
    invoke_graph,
    refactorer_advancing,
    reviewer_accepting,
    reviewer_rejecting,
    reviewer_with_phases,
)


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
    def test_graph_has_exactly_the_expected_nodes(self):
        assert set(build_graph(make_llm_configs()).nodes) == set(EXPECTED_NODES)

    def test_node_names_match_phase_names(self):
        """Node names should correspond to phase names in the state."""
        assert NODE_ARCHITECT == "architect"
        assert NODE_DRIVER_RED == "driver_red"
        assert NODE_DRIVER_GREEN == "driver_green"
        assert NODE_REFACTORER == "refactorer"
        assert NODE_PAUSE == "awaiting_human"
        assert NODE_DONE == "done"
        assert NODE_BLOCKED == "blocked"


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

        reject_red = reviewer_rejecting(CheckpointType.RED_CHECK, record=calls)

        with pytest.raises(GraphRecursionError):
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=5,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=reject_red,
            )

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

        with pytest.raises(GraphRecursionError):
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=6,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=accept_red_only,
            )

        assert "driver_green" in calls

    def test_green_check_reject_retries_driver_green_not_refactorer(self, tmp_path):
        calls = []

        accept_red_reject_green = reviewer_rejecting(CheckpointType.GREEN_CHECK, record=calls)

        with pytest.raises(GraphRecursionError):
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=8,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=accept_red_reject_green,
            )

        assert calls.count("reviewer_red-check") == 1, "RED accepted exactly once"
        assert calls.count("reviewer_green-check") >= 2, "GREEN rejected into retry"
        assert calls.count("driver_green") >= 2
        # A GREEN rejection that skipped ahead would surface as a refactor
        # review without a second GREEN attempt.
        assert "reviewer_refactor-check" not in calls

    def test_refactor_check_reject_retries_refactorer_not_done(self, tmp_path):
        calls = []

        accept_through_green = reviewer_rejecting(CheckpointType.REFACTOR_CHECK, record=calls)

        with pytest.raises(GraphRecursionError):
            invoke_graph(
                make_run_state(),
                tmp_path,
                recursion_limit=9,
                architect=architect_advancing(),
                driver=driver_advancing(calls),
                reviewer=accept_through_green,
                refactorer=refactorer_advancing(calls),
            )

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
        sequence, events = [], []
        final = invoke_graph(
            make_run_state(), tmp_path, recursion_limit=10,
            record=sequence, on_node_event=events.append,
        )

        assert final["status"] == RunStatus.DONE
        assert final["phase"] == "done"
        assert sequence == [
            "architect", "driver_red", "reviewer_red-check", "driver_green",
            "reviewer_green-check", "refactorer", "reviewer_refactor-check",
        ]
        assert [event["node"] for event in events if event["type"] == "node_start"] == [
            "architect", "driver_red", "reviewer_red", "driver_green",
            "reviewer_green", "refactorer", "reviewer_refactor",
        ]
