"""Graph handling of malformed role output and typed Driver results."""


from battalion.nodes.driver import InvalidModeOutput
from battalion.nodes.driver import run_driver
from battalion.nodes.refactorer import MalformedRefactorerOutput
from battalion.graph import NODE_DRIVER_GREEN, NODE_DRIVER_RED, NODE_PAUSE, NODE_REFACTORER
from battalion.state.models import RunState, RunStatus
from battalion.scope.tool_binding import ScopeViolationError
from support.state import make_run_state
from support.graph import invoke_graph, refactorer_advancing, reviewer_accepting, resume_graph


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
