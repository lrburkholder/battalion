const find = (items, key, value) => {
  const result = items.find((item) => item[key] === value);
  if (!result) throw new Error(`fixture is missing ${key}=${value}`);
  return result;
};

export class ElectronBenchmarkAdapter {
  constructor(fixture) {
    if (fixture.fixture_id !== "BTN-37-desktop-v1") throw new Error(`unsupported fixture: ${fixture.fixture_id}`);
    this.fixture = fixture;
    this.actionResults = new Map();
  }

  observe(stepId) {
    const completed = find(this.fixture.runs, "run_id", "20000000-0000-4000-8000-000000000037");
    const active = find(this.fixture.runs, "run_id", "10000000-0000-4000-8000-000000000037");
    const executions = completed.execution_record.node_executions;
    const driver = find(executions, "execution_id", "node-driver-green-1");
    const calls = executions.flatMap((execution) => execution.llm_calls);
    const action = (id) => find(this.fixture.actions, "action_id", id);

    switch (stepId) {
      case "work": return { project_id: this.fixture.projects[0].project_id, ticket_id: active.ticket_id };
      case "history": return { run_ids: this.fixture.runs.map((run) => run.run_id) };
      case "execution": return { execution_id: driver.execution_id, artifact: driver.artifact_provenance[0].path };
      case "cost": {
        const known = calls.find((call) => call.cost !== null);
        return { known_cost: known.cost, currency: known.cost_currency, unknown_cost_calls: calls.filter((call) => call.cost === null).length };
      }
      case "provenance": return {
        revision: driver.code_provenance.base_commit_object_id,
        context_hash: driver.input_references[0].sha256,
        exact_workspace_reconstructable: driver.code_provenance.exact_workspace_reconstructable,
      };
      case "live": return {
        sequences: this.fixture.observations.map((event) => event.sequence),
        kinds: this.fixture.observations.map((event) => event.kind),
        node: this.fixture.observations.find((event) => event.node).node,
      };
      case "reconnect": {
        const durable = this.fixture.observations.find((event) => event.category === "durable");
        return { durable_first: true, phase: durable.payload.phase, barrier_sequence: Math.max(...this.fixture.observations.map((event) => event.sequence)) };
      }
      case "interrupt": {
        const resolved = action("resolve-interrupt");
        this.actionResults.set(resolved.action_id, "in-progress");
        return { action_id: resolved.action_id, result: "in-progress" };
      }
      case "candidate": {
        const reviewed = action("review-candidate");
        this.actionResults.set(reviewed.action_id, "promoted");
        return { candidate_id: this.fixture.candidates[0].instinct_id, disposition: "promoted" };
      }
      case "correction":
      case "design": {
        const queued = action(stepId === "correction" ? "queue-correction" : "queue-design-decision");
        this.actionResults.set(queued.action_id, "queued");
        return { action_id: queued.action_id, target: queued.target, timing: queued.payload.timing };
      }
      case "provider-guard": return { provider_mode: this.fixture.provider_mode, provider_calls: 0 };
      default: throw new Error(`unknown scenario step: ${stepId}`);
    }
  }
}

export function runScenario(fixture, scenario) {
  const adapter = new ElectronBenchmarkAdapter(fixture);
  return {
    schema_version: "1.0",
    fixture_id: fixture.fixture_id,
    framework: "electron",
    entries: scenario.map((step) => ({ step_id: step.step_id, observed: adapter.observe(step.step_id) })),
  };
}

