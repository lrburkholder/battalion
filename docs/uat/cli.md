# CLI UAT plan

**Status:** Draft for maintainer review; not a shipped product contract.

## Purpose

Exercise Battalion as an installed command-line application against a
disposable local project. This plan is intentionally separate from unit tests:
it verifies packaging, durable state, real provider interaction, human
interrupt handling, and the observable command-line experience.

## Preconditions

- Python 3.11 or newer.
- A disposable directory; do not run this plan in a repository with work to
  preserve.
- Git installed.
- A working inference provider. The examples below use locally installed
  Ollama models and keep Driver and Reviewer distinct.
- Battalion installed from the candidate checkout:

  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  python -m pip install -e "C:\src\battalion[dev]"
  battalion --help
  ```

Record the output of `battalion --help`. It must list `run`, `resume`,
`status`, and `setup` without an import or encoding error.

## 1. Set up a disposable project

```powershell
mkdir battalion-cli-uat
cd battalion-cli-uat
git init

battalion setup `
  --model-architect ollama/mistral:latest `
  --model-driver ollama/north-mini-code-1.0:latest `
  --model-reviewer ollama/lfm2.5:latest `
  --model-refactorer ollama/north-mini-code-1.0:latest `
  --validate
```

Expected result: setup persists `battalion.config.yaml`, validates the selected
provider, and rejects a configuration that gives Driver and Reviewer the same
model.

## 2. Full happy path

Run a deliberately small ticket:

```powershell
battalion run BTN-UAT-1 `
  --spec "Create src/greeting.py with greet(name: str) returning Hello, {name}! and src/test_greeting.py proving it. Return strict JSON for Driver and Refactorer responses." `
  --trace-output .battalion\traces\BTN-UAT-1.jsonl `
  --budget 12
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
battalion status <RUN_UUID> --human --costs
python -m pytest -q
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
- The terminal retains each completed node's trace in scrollback, and the
  JSONL trace contains node-associated `reasoning` and `token` events for
  post-run review.

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

```powershell
battalion run BTN-UAT-2 --spec "Create a tiny pure-Python module and test." --checkpoint driver
battalion status <RUN_UUID> --human
battalion resume <RUN_UUID> --resolution "Architecture reviewed and approved"
```

The current implementation treats `driver` as a pause before Driver begins,
which is after Architect completes. Confirm that the initial command records
`awaiting-human`, the resolution becomes durable, and resume continues through
the canonical graph path.

## 4. Provider-failure recovery

Configure an unavailable local model or disconnect the selected provider, then
start a new run. Verify all of the following:

- The run pauses with `infra-failure`; it does not print a Python traceback.
- `battalion status <RUN_UUID> --human` renders the provider error and a
  resumable run.
- After correcting the configuration, `battalion resume` records the supplied
  resolution and retries from the saved target.

## 5. Role-contract correction: simple Hello World

Use a disposable Hello World ticket and a controlled Driver response that first
returns a test file during GREEN, then returns the required implementation on
its next response. The first candidate must be valid Driver JSON; it is the
artifact category, not JSON validity, that is intentionally wrong.

```powershell
battalion run BTN-UAT-5 `
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
- a duplicate run invocation;
- a foreground interruption after a durable node checkpoint; and
- malformed, empty, or non-JSON Architect, Driver, Reviewer, or Refactorer
  output.

Malformed, empty, or non-JSON role output must pause with `infra-failure`,
retain the actionable error in `battalion status <RUN_UUID> --human`, and leave
a resumable run. It must not emit a Python traceback or leave the run
`in-progress`. Pre-write RED/GREEN artifact-category violations instead follow
the bounded correction flow above.

## Evidence to retain

Retain the command transcript, `battalion.config.yaml` with any secrets
removed, `.battalion/state/<RUN_UUID>.json`, `plan.md`, generated source and
test files, and the final `pytest` result. `--trace-output` is opt-in raw
provider text for the local operator: it may be sensitive, is not acceptance
evidence, and must not be shared with credentials or raw prompt/source payloads.
