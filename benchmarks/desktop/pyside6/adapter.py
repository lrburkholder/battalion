"""Framework-only projection of BTN-37 data into an acceptance trace."""

from __future__ import annotations

from typing import Any


FIXTURE_ID = "BTN-37-desktop-v1"
RUN_ACTIVE = "10000000-0000-4000-8000-000000000037"
RUN_COMPLETE = "20000000-0000-4000-8000-000000000037"


def _by_id(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    try:
        return next(item for item in items if item[key] == value)
    except StopIteration as error:
        raise ValueError(f"fixture is missing {key}={value}") from error


class PySideBenchmarkAdapter:
    """State-local adapter; it has no Battalion application authority."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        if fixture.get("fixture_id") != FIXTURE_ID:
            raise ValueError(f"unsupported fixture: {fixture.get('fixture_id')}")
        self.fixture = fixture
        self.action_results: dict[str, str] = {}

    def observe(self, step_id: str) -> dict[str, Any]:
        completed = _by_id(self.fixture["runs"], "run_id", RUN_COMPLETE)
        active = _by_id(self.fixture["runs"], "run_id", RUN_ACTIVE)
        executions = completed["execution_record"]["node_executions"]
        driver = _by_id(executions, "execution_id", "node-driver-green-1")
        calls = [call for execution in executions for call in execution["llm_calls"]]
        action = lambda action_id: _by_id(self.fixture["actions"], "action_id", action_id)

        if step_id == "work":
            return {"project_id": self.fixture["projects"][0]["project_id"], "ticket_id": active["ticket_id"]}
        if step_id == "history":
            return {"run_ids": [run["run_id"] for run in self.fixture["runs"]]}
        if step_id == "execution":
            return {"execution_id": driver["execution_id"], "artifact": driver["artifact_provenance"][0]["path"]}
        if step_id == "cost":
            known = next(call for call in calls if call["cost"] is not None)
            return {
                "known_cost": known["cost"],
                "currency": known["cost_currency"],
                "unknown_cost_calls": sum(call["cost"] is None for call in calls),
            }
        if step_id == "provenance":
            return {
                "revision": driver["code_provenance"]["base_commit_object_id"],
                "context_hash": driver["input_references"][0]["sha256"],
                "exact_workspace_reconstructable": driver["code_provenance"]["exact_workspace_reconstructable"],
            }
        if step_id == "live":
            observations = self.fixture["observations"]
            return {
                "sequences": [event["sequence"] for event in observations],
                "kinds": [event["kind"] for event in observations],
                "node": next(event["node"] for event in observations if event.get("node")),
            }
        if step_id == "reconnect":
            observations = self.fixture["observations"]
            durable = next(event for event in observations if event["category"] == "durable")
            return {
                "durable_first": True,
                "phase": durable["payload"]["phase"],
                "barrier_sequence": max(event["sequence"] for event in observations),
            }
        if step_id == "interrupt":
            resolved = action("resolve-interrupt")
            self.action_results[resolved["action_id"]] = "in-progress"
            return {"action_id": resolved["action_id"], "result": "in-progress"}
        if step_id == "candidate":
            reviewed = action("review-candidate")
            self.action_results[reviewed["action_id"]] = "promoted"
            return {"candidate_id": self.fixture["candidates"][0]["instinct_id"], "disposition": "promoted"}
        if step_id in {"correction", "design"}:
            action_id = "queue-correction" if step_id == "correction" else "queue-design-decision"
            queued = action(action_id)
            self.action_results[queued["action_id"]] = "queued"
            return {
                "action_id": queued["action_id"],
                "target": queued["target"],
                "timing": queued["payload"]["timing"],
            }
        if step_id == "provider-guard":
            return {"provider_mode": self.fixture["provider_mode"], "provider_calls": 0}
        raise ValueError(f"unknown scenario step: {step_id}")


def run_scenario(fixture: dict[str, Any], scenario: list[dict[str, Any]]) -> dict[str, Any]:
    adapter = PySideBenchmarkAdapter(fixture)
    return {
        "schema_version": "1.0",
        "fixture_id": fixture["fixture_id"],
        "framework": "pyside6",
        "entries": [
            {"step_id": step["step_id"], "observed": adapter.observe(step["step_id"])}
            for step in scenario
        ],
    }

