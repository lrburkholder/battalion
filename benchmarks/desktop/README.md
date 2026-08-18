# Desktop Framework Benchmark Fixture

This directory is the BTN-37 control case for the disposable Tauri, PySide6,
and Electron spikes. It is test infrastructure, not production application
policy. All frameworks must use the exported data, ordered scenario, acceptance
validator, and measurement categories unchanged.

Export the deterministic inputs from the repository root:

```console
python -m benchmarks.desktop.export <spike-workspace>/benchmark-input
```

The command creates `fixture.json`, `scenario.json`, and
`measurement-template.json`. It performs no provider call, reads no provider
configuration, and requires no credentials. The fixture contains canonical
run-state-shaped history, node attempts, artifact digests, known and unknown
cost evidence, prompt/code/context provenance, an interrupt, a Recon candidate,
live observations, and simulated operator actions.

Each prototype must drive the scenario in `scenario.json` in order and write a
trace with this shape:

```json
{
  "schema_version": "1.0",
  "fixture_id": "BTN-37-desktop-v1",
  "framework": "tauri",
  "entries": [
    {"step_id": "work", "observed": {"project_id": "...", "ticket_id": "BTN-102"}}
  ]
}
```

`observed` may contain additional framework evidence, but it must contain the
expected fields in `scenario.json`. Validate the completed trace with the same
script for every framework:

```console
python -m benchmarks.desktop.acceptance <spike-workspace>/trace.json
```

The Correction and Design-decision records are simulations of the accepted
future interaction contract. They must remain queued for the next eligible
attempt and must not be implemented here as graph mutations, Reviewer steering,
or new application operations. Likewise, reconnect must render durable state
before post-barrier live observations.

## Measurement procedure

Use the exported measurement template without adding, removing, or renaming a
category. Record raw observations and environment details, not only conclusions.
Run packaging and resource measurements against release builds on the same
machine and OS image. Run five cold starts and five full scenarios, retaining
all samples and reporting the median. Record exact build and measurement
commands, tool versions, artifact hashes, and failures.

Accessibility combines automated checks with a keyboard-only pass and
screen-reader inspection. Failure recovery injects a worker crash, client
restart, missed transient event, and malformed fixture. Permission evidence
must inventory effective release permissions and demonstrate denial of
undeclared filesystem, shell, and network access. Learning and complexity
records must separate framework-specific work from the shared fixture and
boundary adapter.

Do not change fixture data or acceptance expectations to accommodate a
framework. If the control case is defective, fix BTN-37 once and rerun all
three spikes from the same revision.

## Outcome

The completed [Tauri](tauri/evidence/findings.md),
[PySide6](pyside6/evidence/findings.md), and
[Electron](electron/evidence/findings.md) records retain both measurements and
known limitations. [ADR-0022](../../docs/adrs/adr0022.md) selects PySide6 with
Qt Widgets for production presentation. The spikes remain disposable benchmark
evidence and are not production UI modules.
