"""Graph resume routing preserves the human-approved continuation point."""


from datetime import datetime, timezone
from battalion.graph import NODE_DRIVER_GREEN, NODE_REVIEWER_REFACTOR, resume_ticket
from battalion.state.models import InterruptLogEntry, RunStatus
from unittest.mock import patch
from support.state import make_llm_configs, make_run_state
from support.graph import architect_advancing, reviewer_accepting, resume_graph


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
