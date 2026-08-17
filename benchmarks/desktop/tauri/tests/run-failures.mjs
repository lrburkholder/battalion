import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { TauriBenchmarkAdapter } from "../ui/scenario-adapter.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  await readFile(join(here, "..", "ui", "benchmark-input", "fixture.json"), "utf8"),
);

const durableOnly = structuredClone(fixture);
durableOnly.observations = durableOnly.observations.filter(
  (observation) => observation.category === "durable",
);
assert.deepEqual(new TauriBenchmarkAdapter(durableOnly).observe("reconnect"), {
  durable_first: true,
  phase: "driver_green",
  barrier_sequence: 3,
});

assert.throws(
  () => new TauriBenchmarkAdapter({ ...fixture, fixture_id: "malformed" }),
  /unsupported fixture: malformed/,
);

console.log("PASS: Tauri adapter recovered durable state and diagnosed malformed input");
