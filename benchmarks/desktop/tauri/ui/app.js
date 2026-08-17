import { runScenario } from "./scenario-adapter.mjs";

const status = document.querySelector("#status");
const steps = document.querySelector("#steps");
const traceOutput = document.querySelector("#trace");

async function loadJson(name) {
  const response = await fetch(`./benchmark-input/${name}`);
  if (!response.ok) throw new Error(`unable to load ${name}: ${response.status}`);
  return response.json();
}

async function permissionProbes(invoke) {
  async function denied(operation) {
    try {
      await operation();
      return false;
    } catch {
      return true;
    }
  }

  return {
    filesystemDenied: await denied(() => invoke("plugin:fs|read_text_file", { path: "C:\\Windows\\win.ini" })),
    shellDenied: await denied(() => invoke("plugin:shell|execute", { program: "cmd", args: ["/c", "exit"] })),
    networkDenied: await denied(() => fetch("https://example.invalid/btn-38-permission-probe")),
  };
}

async function start() {
  try {
    const [fixture, scenario] = await Promise.all([
      loadJson("fixture.json"),
      loadJson("scenario.json"),
    ]);
    const trace = runScenario(fixture, scenario);
    steps.replaceChildren(...trace.entries.map((entry) => {
      const item = document.createElement("li");
      item.tabIndex = 0;
      item.innerHTML = `<strong>${entry.step_id}</strong><span>complete</span>`;
      return item;
    }));
    traceOutput.textContent = JSON.stringify(trace, null, 2);
    status.textContent = `${trace.entries.length} of ${scenario.length} steps complete`;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const invoke = window.__TAURI__?.core.invoke;
    if (invoke) {
      const boundary = await invoke("boundary_contract");
      const probes = boundary.permission_probe ? await permissionProbes(invoke) : null;
      await invoke("benchmark_complete", { probes });
    }
  } catch (error) {
    status.textContent = `Benchmark failed: ${error.message}`;
    status.dataset.state = "error";
  }
}

start();

