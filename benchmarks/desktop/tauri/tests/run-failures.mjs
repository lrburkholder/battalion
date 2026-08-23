import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { TauriBenchmarkAdapter } from "../ui/scenario-adapter.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const input = process.argv[2] ? resolve(process.argv[2]) : join(root, "ui", "benchmark-input");
const fixture = JSON.parse(
  await readFile(join(input, "fixture.json"), "utf8"),
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
