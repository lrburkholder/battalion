import { runScenario } from "../adapter.mjs";

const status = document.querySelector("#status");
const steps = document.querySelector("#steps");
const traceOutput = document.querySelector("#trace");

try {
  const { fixture, scenario } = await window.benchmark.load();
  const trace = runScenario(fixture, scenario);
  steps.replaceChildren(...trace.entries.map((entry) => {
    const item = document.createElement("li");
    item.tabIndex = 0;
    const name = document.createElement("strong");
    name.textContent = entry.step_id;
    const result = document.createElement("span");
    result.textContent = "complete";
    item.append(name, result);
    return item;
  }));
  traceOutput.textContent = JSON.stringify(trace, null, 2);
  status.textContent = `${trace.entries.length} of ${scenario.length} steps complete`;
  window.benchmark.complete(trace);
} catch (error) {
  status.textContent = `Benchmark failed: ${error.message}`;
  status.dataset.state = "error";
}

