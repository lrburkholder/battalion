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
  files.

The last case is a known failing condition in the current candidate: the
Driver raises `InvalidModeOutput`, which escapes as a traceback and can leave
the run `in-progress`. The required eventual behavior is a documented durable
interrupt with actionable context, not a generic crash. Do not mark the CLI
full-flow UAT complete until that condition is repaired.

## Evidence to retain

Retain the command transcript, `battalion.config.yaml` with any secrets
removed, `.battalion/state/<RUN_UUID>.json`, `plan.md`, generated source and
test files, and the final `pytest` result. Do not include provider credentials
or raw prompt/source payloads in shared evidence.
