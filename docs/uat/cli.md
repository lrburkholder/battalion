# CLI UAT plan

**Status:** Prepared for operator review under BTN-170; approval and final live
execution are separate evidence. This script is not a release-readiness claim.

Before formal execution, record script revision, reviewer, review date, and
approval decision in the UAT evidence. Final live/documentation-only acceptance
belongs to [BTN-129](https://github.com/lrburkholder/battalion/issues/203) on the
main-based candidate handed off by [BTN-173](https://github.com/lrburkholder/battalion/issues/271).
Script preparation/review does not wait for that final acceptance.

## Purpose

Exercise Battalion as an installed command-line application against a
disposable local project. This plan is intentionally separate from unit tests:
it verifies packaging, durable state, real provider interaction, human
interrupt handling, and the observable command-line experience.

## Preconditions and artifact record

Use Windows PowerShell and the exact candidate **wheel**, never an editable
checkout. Follow [Getting Started](../getting-started.md) for prerequisites,
download/handoff selection, SHA-256 verification, a new Python 3.11+ environment,
pytest installation, disposable Git project, and provider/model configuration.
No Battalion source directory may be on `sys.path` or `PYTHONPATH`.

Retain this record before any provider call:

| Evidence | Value to record |
| --- | --- |
| Candidate identity | Build/run URL; main source commit; subsequent remediation baseline if any |
| Wheel | Exact artifact filename, expected and actual SHA-256 |
| Provenance | Metadata filename, package version, source revision, tag only if actually tagged |
| Documentation | Published guide URL and guide/script commit; script review decision |
| Environment | OS, PowerShell/Python versions, installed package location, dependency versions |
| Operator | Reviewer/operator identity, date, provider/model choices without secrets |

## 1. Clean-environment, documentation-only onboarding

A new operator follows Getting Started sections 1–5 using only the published
instructions and named artifact handoff, without reading source code or asking
maintainers for missing steps. Do not reuse a developer environment. If there
is no candidate or published artifact, record **blocked**, not passed.

Record every undocumented required step, failed command, unclear prerequisite,
or incorrect expected result as a defect, with guide section, expected/actual
behavior, sanitized transcript, and candidate identity. If assistance is needed,
record the original failure and restart the clean pass after guidance is fixed.
Do not silently repair the environment and count the original pass as success.

Retain wheel import location, CLI help, and packaged prompt smoke output. Verify
setup writes secret-free configuration, checks connectivity, and keeps Driver
and Reviewer distinct. Validation is one model per provider, not a claim that
every role model has passed. The guide's manual checkpoint, printed UUID,
human-readable inspection, durable resolution, and final `done`/pytest evidence
are mandatory. `$Python`, `$Project`, and `$RunId` below are the variables
established by that guide; replace `$RunId` after **each** new Run.

## 2. Full happy path

Start another fresh disposable project using guide sections 3–4; keep its
importable greeting stub and specification. Run without a manual checkpoint:

```powershell
& $Python -m battalion run BTN-UAT-1 --spec ticket.md --budget 20
$RunId = Read-Host 'Paste the printed Run UUID'
```

Record the printed run UUID. The expected successful progression is:

```text
Architect
Driver (RED)
Reviewer (RED)
Driver (GREEN)
Reviewer (GREEN)
Refactorer
Reviewer (refactor)
```

Verify the outcome:

```powershell
& $Python -m battalion status $RunId --human --costs
& $Python -m battalion status $RunId
& $Python -m pytest -q
```

Pass criteria:

- The status is `done` and no interrupt is recorded.
- `plan.md`, `src/greeting.py`, and `src/test_greeting.py` exist.
- Tests pass in the project and the execution record shows all seven phases.
- The status output shows stored model, token, cost, and artifact evidence;
  missing cost is presented as `unknown`, never as zero.
- `battalion status <RUN_UUID> --human --costs` identifies each node's model,
  provider token usage, and bounded streamed reasoning/content character counts
  without embedding raw trace text in `RunState`.
- The terminal retains available node-associated progress. Raw reasoning/token
  text is provider-dependent, not guaranteed evidence for every model.

Optional trace check: only after reviewing the sensitivity warning in Getting
Started, repeat on another disposable project with
`--trace-output .battalion\traces\BTN-UAT-1.jsonl`. Record whether the provider
emits node-associated `reasoning`/`token` observations. Do not require a trace
for acceptance or share raw provider text as routine evidence.

### Prompt-efficiency observation

For this deliberately small ticket, the generated `plan.md` should be 250
words or fewer and the Driver and Refactorer final responses should be direct
JSON rather than commentary. Compare the per-node reasoning/content character
counts and provider tokens in `status --costs` across configured models. Inspect
the optional trace for repeated debate about RED's intentionally missing
implementation or JSON serialization; record that as a UAT finding if it
recurs. Raw provider reasoning remains observable but is provider-controlled,
so its character count is diagnostic evidence—not a deterministic pass/fail
contract.

For Refactorer, an already-clear implementation may validly return
`{"outcome":"no-change","files":{},"reason":"..."}`. That result writes no
files, records `refactorer:no-change` in execution evidence, and still proceeds
to the independent Refactor review.

## 3. Manual checkpoint and resume

Use another fresh project from Getting Started sections 3–4, so the completed
implementation from scenario 2 cannot turn RED into an already-passing test.
Read `plan.md` after inspection and before the resume command below.

```powershell
& $Python -m battalion run BTN-UAT-2 --spec ticket.md --checkpoint driver
$RunId = Read-Host 'Paste the printed Run UUID'
& $Python -m battalion status $RunId --human
& $Python -m battalion resume $RunId --resolution 'Architecture reviewed and approved'
& $Python -m battalion status $RunId --human
```

The current implementation treats `driver` as a pause before Driver begins,
which is after Architect completes. Confirm that the initial command records
`awaiting-human`, the resolution becomes durable, and resume continues through
the canonical graph path.

## 4. Provider-failure recovery

Configure an unavailable local model or disconnect the selected provider, then
start a new run. Verify all of the following:

- A handled provider failure pauses at `awaiting-human` with an `infra-failure`
  interrupt; it does not print a Python traceback. Record the actual status
  and recovery disposition rather than equating the trigger name with status.
- Inspect both human-readable and JSON status for the saved error and resume
  target; record insufficient human-readable diagnostics as a defect.
- After correcting the configuration, `battalion resume` records the supplied
  resolution and retries from the saved target.

## 5. Role-contract correction: simple Hello World

This is a separately prepared controlled-provider scenario, not part of the
documentation-only pass. Record the fixture/service identity, scripted responses,
and reviewer approval. If no bounded fixture is supplied, mark it blocked; do
not improvise by patching the installed wheel or saved Run state.

Use a disposable Hello World ticket and a controlled Driver response that first
returns a test file during GREEN, then returns the required implementation on
its next response. The first candidate must be valid Driver JSON; it is the
artifact category, not JSON validity, that is intentionally wrong.

```powershell
& $Python -m battalion run BTN-UAT-5 `
  --spec "Create src/hello.py with hello() returning Hello World, plus a RED test." `
  --budget 12
```

Pass criteria:

- The CLI says Battalion caught a role-contract violation, confirms the
  prohibited test output was not written, and announces correction/retry of
  Driver GREEN.
- The final Run is `done` after the corrected GREEN attempt, rather than being
  reported as a successful first attempt or an immediate human interrupt.
- The saved execution record contains the rejected GREEN attempt with reason
  code, offending test path, correction attempt number, model identity,
  `mutation_applied: false`, and `resulting_disposition: retry`; the following
  GREEN attempt is accepted and retains its normal token/cost evidence.

Repeat the controlled invalid response once more. The second violation must
stop at the existing human-interrupt path rather than loop indefinitely. A
real scope violation remains an immediate authority interrupt and receives no
automatic correction retry.

## 6. Negative robustness checks

Capture the console output and saved state for each case:

- identical Driver and Reviewer models at setup;
- unknown run ID for `status` and `resume`;
- a repeated ticket invocation (it must mint a distinct Run UUID, not overwrite
  the original Run);
- a foreground interruption after a durable node checkpoint; and
- malformed, empty, or non-JSON Architect, Driver, Reviewer, or Refactorer
  output.

Use approved controlled-provider fixtures for malformed role output, retaining
their exact response and checkpoint. Record an infrastructure failure and
actionable saved error, with no generic crash or abandoned `in-progress` Run.
Do not assume all roles require JSON: Architect's plan and other role contracts
must be respected when constructing the negative case. Pre-write RED/GREEN
artifact-category violations instead follow the bounded correction flow above.

Record interrupted Runs' reported recovery disposition; unknown-outcome attempts
must not be forced into replay. Provider-failure and controlled-output exercises
remain human UAT, not credential-free documentation checks.

## Evidence to retain

Retain the artifact record, script approval, documentation-only defect log,
per-scenario pass/fail/blocked disposition, command transcript,
`battalion.config.yaml` with any secrets
removed, `.battalion/state/<RUN_UUID>.json`, `plan.md`, generated source and
test files, and the final `pytest` result. `--trace-output` is opt-in raw
provider text for the local operator: it may be sensitive, is not acceptance
evidence, and must not be shared with credentials or raw prompt/source payloads.
