import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { runScenario } from "../ui/scenario-adapter.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const input = process.argv[2] ? resolve(process.argv[2]) : join(root, "ui", "benchmark-input");
const output = process.argv[3] ? resolve(process.argv[3]) : join(root, "evidence", "trace.json");
const fixture = JSON.parse(await readFile(join(input, "fixture.json"), "utf8"));
const scenario = JSON.parse(await readFile(join(input, "scenario.json"), "utf8"));
const trace = runScenario(fixture, scenario);

if (trace.entries.length !== scenario.length) throw new Error("scenario was not completed");
await mkdir(dirname(output), { recursive: true });
await writeFile(output, `${JSON.stringify(trace, null, 2)}\n`);
console.log(`PASS: Tauri adapter completed ${trace.entries.length} shared steps`);
