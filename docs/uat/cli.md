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

## 5. Negative robustness checks

Capture the console output and saved state for each case:

- identical Driver and Reviewer models at setup;
- unknown run ID for `status` and `resume`;
- a duplicate run invocation;
- a foreground interruption after a durable node checkpoint; and
- an invalid RED response that contains an implementation file as well as test
  files; and
- malformed, empty, or non-JSON Driver, Reviewer, or Refactorer output.

Each role-output failure must pause with `infra-failure`, identify itself as a
role-output contract violation rather than a provider failure, retain the
actionable error in `battalion status <RUN_UUID> --human`, and leave a resumable
run. On the human-authorized retry, the affected role receives that validation
feedback. It must not emit a Python traceback or leave the run `in-progress`.

## 6. Targeted BTN-129 regression checks

Use a fresh ticket/run for each case. These checks distinguish a Battalion
contract failure from a configured model's inability to follow a deliberately
contradictory fixture prompt.

### Long streamed reasoning on Windows

Run the happy path with a model known to emit a long reasoning stream and keep
the terminal visible throughout. Verify all of the following:

- adjacent reasoning fragments appear beneath one `[reasoning]` label rather
  than repeating the label for each fragment;
- the active panel remains compact (it does not truncate or redraw a growing
  reasoning transcript);
- each completed node's full reasoning transcript remains in scrollback when
  the next node begins; and
- structured Driver/Refactorer output is shown as per-file source panels with
  real line breaks, not as escaped JSON strings; and
- neither the run nor process shutdown prints a `RuntimeWarning` for
  `Logging.async_success_handler`, `thread.py`, or `ProactorEventLoop`.

### Trace output across a resume

Start a run with a Driver checkpoint and one trace file, then resume to that
same file:

```powershell
battalion run BTN-UAT-TRACE `
  --spec "Create a tiny pure-Python module and test." `
  --checkpoint driver `
  --trace-output .battalion\traces\BTN-UAT-TRACE.jsonl

battalion resume <RUN_UUID> `
  --resolution "Resume trace UAT" `
  --trace-output .battalion\traces\BTN-UAT-TRACE.jsonl
```

Confirm the JSONL file appends rather than overwrites, contains the same
`run_ref` with the correct node for Architect before the pause and Driver and
later nodes after resume, and that raw trace text is absent from the saved
`RunState`. A trace `sequence` restarts when `resume` creates a new display;
it is a per-invocation display counter, not a durable global event ID.

### Refactorer artifact-authority rejection

Copy the standard prompts into a disposable override directory, then replace
only `refactorer.md` with a fixture that returns exactly this JSON object:

```json
{"files":{"unrelated.py":"VALUE = 1\n"}}
```

Run the normal small-ticket happy path with `--prompts-dir .\uat-prompts`.
After GREEN succeeds, expect a durable `infra-failure` whose error says the
path was not written by the accepted GREEN Driver. `src/unrelated.py` must not
exist and the GREEN-produced implementation must be unchanged. Restore the
normal Refactorer prompt and resume; it may finish normally or return a valid
no-change result. If the provider does not follow the fixture prompt, retain
that as a model-capability observation rather than treating it as a product
pass.

### Explicit Refactorer no-change

For a trivial passing task, either observe the normal no-op or use a temporary
Refactorer fixture that returns:

```json
{"outcome":"no-change","files":{},"reason":"Already minimal."}
```

The final Refactor review must still run and the run must reach `done`. Inspect
the saved execution record: the Refactorer output reference is
`refactorer:no-change` and it has no Refactorer artifacts.

### Role-contract failure retries the correct node with validation feedback

With a disposable Driver RED or GREEN prompt override, direct the model to
return valid JSON containing both a test file and a production file. The run
must pause without a traceback and must label the result a role-output contract
violation, not a provider error. After restoring the normal prompt, resume and
verify that Battalion retries the affected Driver phase (RED or GREEN), not
Architect or the other Driver phase, and that its context includes the prior
validation error.

### GREEN workspace-snapshot echo

Use a disposable GREEN fixture that returns both its production implementation
and a copy of an accepted RED test. The run must write only the production
file, continue to GREEN review, and record an
`ignore-unchanged-test-echo` role-output-filter activity in the GREEN Driver
execution. Repeat with a changed assertion or changed indentation in that test:
the run must pause with a role-output contract violation and must not write the
test. CRLF versus LF and one final newline are the only tolerated differences.

### Reviewer-feedback retry

Use a fixture that lets GREEN return a production file with a deterministic
syntax error. After GREEN_CHECK rejects it, verify the next Driver GREEN
transcript receives the Reviewer's stored normalized root cause. It must not
receive the raw test log or a cause from RED_CHECK/REFACTOR_CHECK. The corrected
attempt should replace the defective production file rather than repeat it.

### Legacy Windows code page

In a disposable `cmd.exe` session, run:

```bat
chcp 437
battalion --help
battalion status <RUN_UUID> --human --costs
battalion resume <PAUSED_RUN_UUID> --resolution "Windows code-page UAT"
```

Help, status, pause, and resume output must remain readable and must not crash.

## Evidence to retain

Retain the command transcript, `battalion.config.yaml` with any secrets
removed, `.battalion/state/<RUN_UUID>.json`, `plan.md`, generated source and
test files, and the final `pytest` result. For targeted checks, also retain the
prompt override used, the trace JSONL, and evidence that prohibited output files
were not created. `--trace-output` is opt-in raw provider text for the local
operator: it may be sensitive, is not acceptance evidence, and must not be
shared with credentials or raw prompt/source payloads.
