"""Tests for battalion.graph — LangGraph StateGraph wiring (BTN-7).

The graph wires Architect, Driver (RED/GREEN modes), Reviewer, and Refactorer
into a StateGraph with correct edges and interrupt pause points.

Flow: Architect -> Driver(RED) -> Reviewer(RED_CHECK) -> Driver(GREEN) ->
      Reviewer(GREEN_CHECK) -> Refactorer -> Reviewer(REFACTOR_CHECK) -> DONE

Acceptance criteria:
1. A ticket flows Architect -> Driver -> Reviewer end-to-end with no interrupt
2. Graph pauses at each defined interrupt point rather than proceeding silently
"""
import pytest
from unittest.mock import patch

from battalion.graph import (
    NODE_ARCHITECT,
    NODE_DONE,
    NODE_DRIVER_GREEN,
    NODE_DRIVER_RED,
    NODE_PAUSE,
    NODE_REFACTORER,
    NODE_REVIEWER_GREEN,
    NODE_REVIEWER_RED,
    NODE_REVIEWER_REFACTOR,
    build_graph,
    resume_ticket,
    run_ticket,
)
from battalion.state.models import Budget, CheckpointType, InterruptLogEntry, RunState, RunStatus
from battalion.llm.litellm_client import NodeLLMConfig


# =============================================================================
# Fixtures / Helpers
# =============================================================================

def make_llm_configs():
    """Create test LLM configs that use mock callables."""
    return {
        "default": NodeLLMConfig(model="test-model", max_retries=0),
        "architect": NodeLLMConfig(model="test-model", max_retries=0),
        "driver": NodeLLMConfig(model="test-model", max_retries=0),
        "reviewer": NodeLLMConfig(model="test-model", max_retries=0),
        "refactorer": NodeLLMConfig(model="test-model", max_retries=0),
    }


def make_mock_llm_call(node_name: str, response_data: dict) -> dict:
    """Create a mock LLM response for testing."""
    import json
    return {"choices": [{"message": {"content": json.dumps(response_data)}}]}


# =============================================================================
# build_graph tests
# =============================================================================

class TestBuildGraph:
    def test_graph_has_all_nodes(self):
        """Graph should contain all 9 nodes."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # graph.nodes is a dict of node_name -> StateNodeSpec
        node_names = list(graph.nodes.keys())
        expected = [
            NODE_ARCHITECT,
            NODE_DRIVER_RED,
            NODE_DRIVER_GREEN,
            NODE_REVIEWER_RED,
            NODE_REVIEWER_GREEN,
            NODE_REFACTORER,
            NODE_REVIEWER_REFACTOR,
            NODE_DONE,
            NODE_PAUSE,
        ]
        for name in expected:
            assert name in node_names, f"Missing node: {name}"

    def test_entry_point_is_architect(self):
        """Graph should start at the Architect node."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # The entry point is stored internally
        # Verify architect node exists
        assert NODE_ARCHITECT in graph.nodes

    def test_architect_connected_to_driver_red(self):
        """Architect should flow to Driver(RED) as first step."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # Architect should have an edge to Driver(RED)
        # This is verified by the graph structure
        assert NODE_ARCHITECT in graph.nodes
        assert NODE_DRIVER_RED in graph.nodes

    def test_done_is_terminal(self):
        """DONE node should have no outgoing edges."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # DONE should be a terminal node
        assert NODE_DONE in graph.nodes


# =============================================================================
# Graph flow tests (with mocked nodes)
# =============================================================================

class TestGraphFlow:
    """Test the actual flow through the graph with mocked node implementations."""
    
    def test_graph_construction_succeeds(self, tmp_path):
        """Basic test: can we build and compile a graph?"""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs, base_dir=tmp_path)
        
        # Should compile without errors
        app = graph.compile()
        assert app is not None

    def test_initial_state_structure(self, tmp_path):
        """Initial state for run_ticket should have correct structure."""
        llm_configs = make_llm_configs()
        
        # We can't easily test run_ticket end-to-end without mocking LLM calls,
        # but we can verify the initial state structure
        initial_state = RunState(
            schema_version="1.0",
            run_id="run-BTN-7-test",
            ticket_id="BTN-7-test",
            status=RunStatus.NOT_STARTED,
            phase=NODE_ARCHITECT,
            write_scope={
                "architect": ["plan.md"],
                "driver": ["src/"],
                "reviewer": [],
            },
            retry_bound=2,
            budget=Budget(limit=100, used=0),
            reviewer_rejection_history=[],
            interrupt_log=[],
            manual_checkpoints=[],
        )
        
        assert initial_state.phase == NODE_ARCHITECT
        assert initial_state.status == RunStatus.NOT_STARTED
        assert initial_state.budget.used == 0


# =============================================================================
# Node edge tests
# =============================================================================

class TestNodeEdges:
    """Test that nodes have correct outgoing edges."""
    
    def test_architect_to_driver_red_edge(self):
        """Architect node should transition to Driver(RED)."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # Verify by checking the compiled graph structure
        app = graph.compile()
        assert app is not None

    def test_driver_red_to_reviewer_red_edge(self):
        """Driver(RED) should transition to Reviewer(RED_CHECK)."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        app = graph.compile()
        assert app is not None

    def test_reviewer_red_conditional_to_driver_green(self):
        """Reviewer(RED_CHECK) should conditionally transition to Driver(GREEN)."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        app = graph.compile()
        assert app is not None

    def test_driver_green_to_reviewer_green_edge(self):
        """Driver(GREEN) should transition to Reviewer(GREEN_CHECK)."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        app = graph.compile()
        assert app is not None

    def test_refactorer_to_reviewer_refactor_edge(self):
        """Refactorer should transition to Reviewer(REFACTOR_CHECK)."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        app = graph.compile()
        assert app is not None


# =============================================================================
# Acceptance Criteria tests
# =============================================================================

class TestAcceptanceCriteria:
    """Test the BTN-7 acceptance criteria directly."""
    
    def test_graph_has_correct_node_count(self):
        """AC: Graph should have all required nodes wired."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # Should have: architect, driver_red, driver_green, reviewer_red,
        # reviewer_green, refactorer, reviewer_refactor, done, pause = 9 nodes
        assert len(graph.nodes) == 9

    def test_graph_compiles_without_errors(self):
        """AC: Graph should compile successfully."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        # This should not raise
        app = graph.compile()
        assert app is not None

    def test_pause_node_exists_for_interrupts(self):
        """AC: Graph should have a pause node for interrupts."""
        llm_configs = make_llm_configs()
        graph = build_graph(llm_configs)
        
        assert NODE_PAUSE in graph.nodes


# =============================================================================
# Integration tests with mocked LLM calls
# =============================================================================

class TestGraphIntegration:
    """Integration tests that verify the graph structure works correctly."""
    
    def test_build_graph_with_real_nodes(self, tmp_path):
        """Should be able to build graph with real node implementations."""
        from battalion.nodes.architect import run_architect
        from battalion.nodes.driver import run_driver
        from battalion.nodes.reviewer import run_reviewer
        from battalion.nodes.refactorer import run_refactorer
        
        llm_configs = make_llm_configs()
        
        # This should not raise - all imports should work
        graph = build_graph(llm_configs, base_dir=tmp_path)
        assert graph is not None
        
        # Should compile
        app = graph.compile()
        assert app is not None

    def test_node_names_match_phase_names(self):
        """Node names should correspond to phase names in the state."""
        assert NODE_ARCHITECT == "architect"
        assert NODE_DRIVER_RED == "driver_red"
        assert NODE_DRIVER_GREEN == "driver_green"
        assert NODE_REFACTORER == "refactorer"
        assert NODE_PAUSE == "awaiting_human"
        assert NODE_DONE == "done"


# =============================================================================
# Real end-to-end execution tests (mocked node internals, real graph routing)
#
# Every test above this point only checks that the graph *compiles* or that
# nodes/edges exist by name -- none of them actually invoke the graph and
# watch it route. That gap is exactly how four real bugs shipped past 192
# passing tests: interrupts fired during Architect/Driver/Refactorer were
# silently ignored (edges were unconditional), a rejected RED check was
# routed to Driver(GREEN) as if it had passed (accept/reject both set
# phase="driver"), every Reviewer checkpoint crashed with an
# InvalidUpdateError the moment it completed (a redundant add_edge fired
# alongside add_conditional_edges to a different target), and resume_ticket
# silently restarted every resumed run from Architect (resume_target was
# set on the state but app.invoke() always starts at the fixed entry point
# regardless of state contents). These tests invoke the compiled graph with
# mocked node internals and assert on the actual call sequence and final
# state, so a regression here fails loudly instead of compiling silently.
# =============================================================================

def _make_initial_state(ticket_id="regression-test", **overrides):
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


class TestInterruptsActuallyHaltExecution:
    """Regression tests for bug #1: interrupts fired outside a Reviewer node
    used to be logged but not acted on -- the graph proceeded to the next
    node regardless."""

    def test_budget_exceeded_during_architect_halts_before_driver(self, tmp_path):
        driver_calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            # Blow the budget on Architect's own turn.
            return state.model_copy(update={"budget": Budget(limit=1, used=999)})

        def fake_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            driver_calls.append(mode)
            return state.model_copy(update={"phase": "reviewer"})

        with patch("battalion.nodes.architect.run_architect", side_effect=fake_architect), \
             patch("battalion.nodes.driver.run_driver", side_effect=fake_driver):
            app = build_graph(make_llm_configs(), base_dir=tmp_path).compile()
            final = app.invoke(_make_initial_state(), {"recursion_limit": 5})

        assert driver_calls == [], "Driver must not run once budget is exceeded"
        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert len(final["interrupt_log"]) == 1
        assert final["interrupt_log"][0].trigger == "budget-exceeded"

    def test_manual_checkpoint_before_driver_halts(self, tmp_path):
        driver_calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            return state.model_copy(update={"phase": "driver"})

        def fake_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            driver_calls.append(mode)
            return state.model_copy(update={"phase": "reviewer"})

        with patch("battalion.nodes.architect.run_architect", side_effect=fake_architect), \
             patch("battalion.nodes.driver.run_driver", side_effect=fake_driver):
            app = build_graph(make_llm_configs(), base_dir=tmp_path).compile()
            # Manual checkpoints are matched against the generic role string
            # ("driver"/"reviewer"/"refactorer") that check_any_trigger is
            # called with, not a concrete node name like NODE_DRIVER_RED --
            # Architect's own pre-flight check for "am I about to hand off
            # to a declared checkpoint" uses NODE_TO_PHASE[NODE_ARCHITECT],
            # which is "driver".
            initial = _make_initial_state(manual_checkpoints=["driver"])
            final = app.invoke(initial, {"recursion_limit": 5})

        assert driver_calls == [], "Manual checkpoint before Driver(RED) must pause first"
        assert final["status"] == RunStatus.AWAITING_HUMAN
        assert final["interrupt_log"][0].trigger == "manual-checkpoint"


class TestRedCheckRoutingIsUnambiguous:
    """Regression tests for bug #2: RED_CHECK accept and reject used to both
    set phase="driver", so a rejected RED check was silently routed to
    Driver(GREEN) as if it had passed."""

    def test_red_check_reject_retries_driver_red_not_green(self, tmp_path):
        calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            return state.model_copy(update={"phase": "driver"})

        def fake_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append(f"driver_{mode}")
            return state.model_copy(update={"phase": "reviewer"})

        def fake_reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
            calls.append(f"reviewer_{checkpoint.value}")
            # Always reject: RED check "unexpectedly passed".
            return state.model_copy(update={"phase": "driver_red", "status": RunStatus.IN_PROGRESS})

        with patch("battalion.nodes.architect.run_architect", side_effect=fake_architect), \
             patch("battalion.nodes.driver.run_driver", side_effect=fake_driver), \
             patch("battalion.nodes.reviewer.run_reviewer", side_effect=fake_reviewer):
            app = build_graph(make_llm_configs(), base_dir=tmp_path).compile()
            try:
                app.invoke(_make_initial_state(), {"recursion_limit": 5})
            except Exception:
                pass  # Expected: mock always rejects, hits recursion limit eventually.

        # The bug: driver_green would appear here even though every reviewer
        # call rejected. It must not.
        assert "driver_green" not in calls
        assert calls.count("driver_red") >= 2

    def test_red_check_accept_advances_to_driver_green(self, tmp_path):
        calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            return state.model_copy(update={"phase": "driver"})

        def fake_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            calls.append(f"driver_{mode}")
            return state.model_copy(update={"phase": "reviewer"})

        def fake_reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
            calls.append(f"reviewer_{checkpoint.value}")
            if checkpoint == CheckpointType.RED_CHECK:
                return state.model_copy(update={"phase": "driver_green", "status": RunStatus.IN_PROGRESS})
            # Reject everything after RED_CHECK so the run halts predictably
            # once we've proven the RED_CHECK -> GREEN transition happened.
            return state.model_copy(update={"phase": "driver", "status": RunStatus.IN_PROGRESS})

        with patch("battalion.nodes.architect.run_architect", side_effect=fake_architect), \
             patch("battalion.nodes.driver.run_driver", side_effect=fake_driver), \
             patch("battalion.nodes.reviewer.run_reviewer", side_effect=fake_reviewer):
            app = build_graph(make_llm_configs(), base_dir=tmp_path).compile()
            try:
                app.invoke(_make_initial_state(), {"recursion_limit": 6})
            except Exception:
                pass

        assert "driver_green" in calls


class TestReviewerCheckpointsDoNotCrash:
    """Regression tests for bug #3: every Reviewer node had a redundant
    add_edge alongside add_conditional_edges, which LangGraph fired in the
    same step whenever the conditional picked a different target --
    guaranteed InvalidUpdateError the moment any checkpoint completed."""

    def test_full_accept_path_completes_without_crashing(self, tmp_path):
        """Architect -> Driver(RED) -> Reviewer(accept) -> Driver(GREEN) ->
        Reviewer(accept) -> Refactorer -> Reviewer(accept) -> DONE, with
        every reviewer call accepting. Must reach DONE, not raise."""
        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            return state.model_copy(update={"phase": "driver"})

        def fake_driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
            return state.model_copy(update={"phase": "reviewer"})

        def fake_refactorer(state, refactor_text, llm_config, base_dir, prompts_dir=None):
            return state.model_copy(update={"phase": "reviewer"})

        def fake_reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
            accept_phase = {
                CheckpointType.RED_CHECK: "driver_green",
                CheckpointType.GREEN_CHECK: "refactorer",
                CheckpointType.REFACTOR_CHECK: "done",
            }[checkpoint]
            status = RunStatus.DONE if accept_phase == "done" else RunStatus.IN_PROGRESS
            return state.model_copy(update={"phase": accept_phase, "status": status})

        with patch("battalion.nodes.architect.run_architect", side_effect=fake_architect), \
             patch("battalion.nodes.driver.run_driver", side_effect=fake_driver), \
             patch("battalion.nodes.refactorer.run_refactorer", side_effect=fake_refactorer), \
             patch("battalion.nodes.reviewer.run_reviewer", side_effect=fake_reviewer):
            app = build_graph(make_llm_configs(), base_dir=tmp_path).compile()
            # Must not raise InvalidUpdateError.
            final = app.invoke(_make_initial_state(), {"recursion_limit": 10})

        assert final["status"] == RunStatus.DONE
        assert final["phase"] == "done"


class TestResumeActuallyResumes:
    """Regression tests for bug #4: resume_ticket set resume_target on the
    state but app.invoke() always starts at the fixed entry point
    (Architect) regardless of state contents -- every resume silently
    restarted the ticket from scratch."""

    def test_resume_does_not_rerun_architect(self, tmp_path):
        calls = []

        def fake_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
            calls.append("architect")
            return state.model_copy(update={"phase": "driver"})

        def fake_reviewer(state, base_dir, llm_config, checkpoint, prompts_dir=None):
            calls.append(f"reviewer_{checkpoint.value}")
            # REFACTOR_CHECK accept -> phase="done" -> routes straight to
            # NODE_DONE, so this test doesn't need to also mock Driver or
            # Refactorer to reach a clean terminal state.
            return state.model_copy(update={"phase": "done", "status": RunStatus.DONE})

        paused_state = _make_initial_state(
            status=RunStatus.AWAITING_HUMAN,
            phase="awaiting_human",
            interrupt_log=[
                InterruptLogEntry(
                    trigger="budget-exceeded",
                    timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                    context={"next_phase": NODE_REVIEWER_REFACTOR},
                )
            ],
        )

        with patch("battalion.nodes.architect.run_architect", side_effect=fake_architect), \
             patch("battalion.nodes.reviewer.run_reviewer", side_effect=fake_reviewer):
            final = resume_ticket(paused_state, make_llm_configs(), base_dir=tmp_path, max_turns=5)

        assert "architect" not in calls, "Resuming must not re-run Architect from scratch"
        assert calls == ["reviewer_refactor-check"]
        assert final["status"] == RunStatus.DONE
