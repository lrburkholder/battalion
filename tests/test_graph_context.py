"""Graph execution context is passed to the appropriate role boundary."""


import pytest
from battalion.context import MAX_CONTEXT_CHARS, driver_context, refactorer_context
from battalion.graph import run_ticket
from battalion.state.models import RunStatus
from unittest.mock import patch
from support.state import make_llm_configs, make_run_state
from support.graph import invoke_graph


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
