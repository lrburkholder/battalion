# Troubleshooting and recovery

## Collect diagnostics first

Before retrying, record the artifact filename, SHA-256, package version, source
commit, build/release URL, OS/architecture, and guide revision from the
[Getting Started artifact checks](getting-started.md#1-choose-an-available-artifact).
Keep the exact error and time, absolute project path, ticket ID, canonical Run
UUID, status, phase, last interrupt, and worker state. A printed UUID alone does
not prove a state file was saved. If startup failed before a Run existed, record
that instead of inventing an ID.

These commands use Windows PowerShell 5.1 or 7. Set `$Python` to the installed
CLI environment's Python, **not** `BattalionWorker.exe`. Do not use a source
checkout as an artifact repair. Keep collected evidence local until sanitized.

<!-- check:diagnostics -->
```powershell
$Python = (Resolve-Path -LiteralPath (Read-Host 'Installed CLI Python path')).Path
$Project = (Resolve-Path -LiteralPath (Read-Host 'Project directory')).Path
Set-Location -LiteralPath $Project
& $Python --version
& $Python -I -c "import battalion; from importlib.metadata import version; print(version('battalion')); print(battalion.__file__)"
$RunId = Read-Host 'Canonical Run UUID (only if a Run was created)'
[guid]::Parse($RunId) | Out-Null
& $Python -m battalion status $RunId --human
```

Use the same project for every status/resume command; `status` has no
`--base-dir` option. For legacy Runs, copy their actual saved identifier instead
of applying the UUID check. Do not manufacture a `run-BTN-*` ID for a new Run.
When human status lacks detail, inspect the structured record:

<!-- check:evidence -->
```powershell
& $Python -m battalion status $RunId
& $Python -m battalion status $RunId --human --costs
$WorkerFile = Join-Path $Project ('.battalion/workers/' + $RunId + '.json')
if (Test-Path -LiteralPath $WorkerFile) {
    Get-Content -LiteralPath $WorkerFile -Raw | ConvertFrom-Json |
        Select-Object run_id, worker_id, status, pid, updated_at, state_path, error
}
```

The worker file is last-recorded evidence, not a liveness probe. In desktop,
select the same UUID in Work/History and use **Project -> Refresh authoritative
state** (`Ctrl+R`) to reconcile it. CLI foreground Runs may have no worker file.
See [worker recovery](#worker-recovery) before starting another process.

Collect only a bounded excerpt around the failure. Candidate builds containing
BTN-164 retain Reviewer command, temporary working-directory identity, test
counts, classification, return code, duration, cleanup disposition, and at most
64 KiB from each stdout/stderr stream, with truncation metadata. The scratch
directory is normally removed; its recorded path is not a backup. Detached
workers discard stdout/stderr; there is no automatic worker console-log file.
Keep any already-captured terminal excerpt; do not promise missing logs can be
reconstructed. Missing cost is **unknown**, not zero.

`--trace-output` is opt-in raw provider text, separate from durable RunState. It
can include secrets, source content, and reasoning. Do not turn it on merely to
retry a failure, publish it, or regard it as authoritative execution evidence.
Before sharing any error, JSON, screenshot, test output, or trace excerpt,
remove credentials, authorization headers, secret URLs, personal data, and
private project content. Preserve originals privately; share a sanitized copy.

## Find the symptom

| What you see | Guide section / stable reason |
| --- | --- |
| Cannot install, launch, or load prompts | [Installation and startup](#installation-startup) |
| Setup rejects credentials, endpoint, or models | [Setup and providers](#setup-provider) |
| `awaiting-human`, `blocked`, or `failed-infra` | [Understand the saved stop](#run-stopped) |
| `infra-failure` | [Execution failure](#infra-failure); also inspect [Reviewer evidence](#reviewer-tests) |
| Invalid JSON, rejected candidate, `role-escalation` | [Role output](#role-output) |
| `manual-checkpoint`, `budget-exceeded` | [Human checkpoints and budget](#human-checkpoints) |
| `out-of-scope-write`, `role-definition-edit` | [Authority stop](#authority-stop) |
| `same-root-cause-twice`, unexpected test result | [Reviewer tests](#reviewer-tests) |
| Crash during resume, `Recovery: recoverable` or `terminal` | [Resume and interventions](#resume-recovery) |
| UI stopped updating, worker `crashed` or apparently stale | [Worker recovery](#worker-recovery) |
| Run not found, unreadable/corrupt state, conflicting Actor | [State and backup](#state-backup) |

## Candidate and release applicability

This guide is prepared for **BTN-171, 2026-08-30**. A version string alone cannot
identify an untagged candidate: match the artifact's source commit and handoff.
The dependency implementations are present on this branch, with canonical
issues still in review at preparation time. This is not a claim that a release
or GitHub Pages deployment already includes them. BTN-173 verifies public
availability after integration. Recheck the linked Issues for later artifacts.

| Baseline / canonical Issue | Operator limitation or changed behavior |
| --- | --- |
| Before [BTN-164](https://github.com/lrburkholder/battalion/issues/260) | Nonzero pytest exits could be accepted as RED, and tests had no bounded timeout. Do not accept collection errors or no-tests as RED evidence. Stop release acceptance and use an identified corrected candidate. |
| Candidate containing BTN-164 | Classified, bounded test execution below applies. It does not make arbitrary project tests safe to execute. |
| Before [BTN-165](https://github.com/lrburkholder/battalion/issues/261) | A crash after resolution/intervention or graph progress may consume authorization or lose the latest progress. Recovery is not guaranteed. Preserve evidence; do not blindly repeat resume or edit state. Seek reviewed recovery or start a new Run only from a reviewed workspace. |
| Candidate containing BTN-165 | Saved resume intent and exact attempt/successor checkpoints support the conditional recovery below. Ambiguous started attempts still cannot be replayed safely. |
| [BTN-163](https://github.com/lrburkholder/battalion/issues/259) | Packaged prompt assets and smoke checks are required. A source-tree workaround does not validate an installed artifact. |
| [BTN-132](https://github.com/lrburkholder/battalion/issues/206) | The current frozen desktop worker excludes pytest and cannot accept Reviewer's `sys.executable -m pytest` invocation. CLI pytest installation does not fix this. ZIP execution acceptance requires a corrected artifact and rerun. |
| [BTN-170](https://github.com/lrburkholder/battalion/issues/266), [BTN-173](https://github.com/lrburkholder/battalion/issues/271) | Artifact-first onboarding and final main/Pages integration govern which guide and artifact to use. Final live CLI/desktop acceptance remains BTN-129/BTN-132. |

<a id="installation-startup"></a>
## Cannot install or start Battalion

Installation failures normally have no Run evidence. Retain installer/shell
output and artifact provenance; leave existing project state untouched.

| Symptom | Safe next action / retry | Do not bypass |
| --- | --- | --- |
| Unsupported Python or missing `venv`/`pip` | Use an approved Python 3.11+ installation and a fresh CLI environment, then repeat the verified-wheel steps in Getting Started. Record interpreter path and version. | Do not ignore Python requirements or switch interpreters mid-install. |
| Unsupported desktop platform | The distributed desktop target is Windows x64. Record OS and architecture; obtain a matching approved artifact or stop. | Do not assume a Windows ZIP supports macOS, Linux, or another architecture. |
| Dependency download/install failure | Preserve the pip error; repair approved index/proxy/cache access and repeat installation in a fresh environment. | Do not disable TLS verification or install a similarly named registry package. |
| Checksum mismatch, absent metadata, wheel/ZIP revision mismatch | Stop before executing it. Redownload from the trusted handoff, verify exact filename/hash and matching version/source commit. Quarantine the suspect copy. | Do not recompute the expected checksum from the suspect file or mix candidate versions. Checksums are not signatures. |
| PowerShell activation blocked | Use the explicit environment Python path shown in Getting Started; activation is unnecessary. | Do not relax machine execution policy. |
| SmartScreen or organizational execution warning | Record the warning and verified provenance; seek the organization's executable approval. Current packaging has no configured signing step. | Do not disable protection or treat a hash match as organizational approval. |
| Prompt asset missing, empty, or not UTF-8 | Run the credential-free checks below. Without an override, replace/report the incomplete artifact. With an intentional `--prompts-dir`, complete that approved directory or omit the override to restore packaged defaults. | The override is authoritative; there is no partial fallback. Do not copy prompts from a checkout to call a broken wheel accepted. |
| Desktop/worker missing or cannot launch | Re-extract the entire verified ZIP into a fresh directory. Preserve `Battalion.dist/Battalion.exe`, sibling `worker/worker_entry.dist/BattalionWorker.exe`, and all DLLs/data. | Do not move only the EXE, substitute another worker, or launch the worker entry point manually for a Run. |

<!-- check:prompt-smoke -->
```powershell
& $Python -m battalion --help
& $Python -m battalion.prompts.smoke
```

For desktop, use `$WorkerExe` from Getting Started and run
`& $WorkerExe --smoke-role-prompts`. Smoke success proves prompt availability,
not provider connectivity or packaged Reviewer execution.

<a id="setup-provider"></a>
## Setup cannot validate the selected models

Setup failure occurs before graph execution and normally writes no new model
configuration. Preserve the error and existing configuration. Read every chosen
role afterward; setup may select a different default Reviewer to maintain
diversity. Use the [published setup commands](getting-started.md#4-choose-models-and-validate-configuration-live-provider-step)
after correcting the specific problem.

| Symptom | What the operator may change / retry | Stop boundary |
| --- | --- | --- |
| `MissingApiKey`, required variable absent | Supply the named provider environment variable through the approved secret procedure, then rerun setup. Launch desktop from that environment too. | A `.env` file is not automatically loaded. Never save the key in model names, YAML, tickets, or transcripts. |
| Connection refused, unavailable endpoint, timeout | Check the selected runtime is running and the intended model is installed; repair approved network/endpoint access. Retry validation only when available. | A local-looking model identifier does not guarantee locality, privacy, or zero cost. Do not change trust/TLS policy to get a green check. |
| `ProviderNotDetected`, unknown/invalid model identifier | Verify the exact provider/model name against the installed runtime/provider and select an accessible model. Keep the original diagnostic. | Do not treat credential changes as a fix for an unknown model or claim every recognized model is supported. |
| `ConnectivityCheckFailed`, authentication/quota/capability error | Inspect the bounded provider error; correct account access, quota, endpoint, or model choice. Validation makes a real completion and may cost money. | Setup checks one selected model per provider, not all role models or full output capabilities. `--no-validate` skips connectivity; it is not proof of readiness and does not bypass credential/diversity checks. |
| `ModelDiversityError`, Driver and Reviewer identical | Choose distinct model identifiers explicitly and revalidate. | Never disable diversity or disguise one model with arbitrary aliases to evade the check. |

<a id="run-stopped"></a>
## The Run stopped before completion

`awaiting-human` with an interrupt entry is a **durable HumanInterrupt**, not a
generic crash. Inspect its trigger, context/error, recorded target, execution
attempts, and any human resolution. A handled provider, malformed role-result,
or test-harness failure may also produce this status. `blocked` can describe a
valid role outcome requiring human context. `failed-infra` is distinct from
successful completion; inspect recovery evidence rather than assuming resume
is safe. An exit code of zero or a worker marked `succeeded` does not prove the
Run is `done`.

Before **every** retry below: ensure no foreground CLI/worker is still acting
on the Run, inspect current durable evidence, and stop if recovery is terminal
or uncertain. Resume authorizes execution and can incur provider costs. It is
not a read-only diagnostic command. Repeating `run` creates a new Run.

<a id="infra-failure"></a>
## Execution failed or the provider stopped responding

For `infra-failure`, inspect the saved error and execution attempt, including
Reviewer process evidence if present. Older CLI wording may say the LLM failed
even when the actual cause is malformed role output or a pytest harness error.
The error context, not that generic label, distinguishes them.

After a handled provider failure with a durable pause, correct credentials,
endpoint availability, account limits, or configured models as appropriate,
retain Driver/Reviewer diversity, and authorize resume from the saved target.
Provider retries may already have consumed tokens/cost; resume is not an
exactly-once provider-call guarantee. If a process disappeared instead of
recording a handled failure, follow [crash recovery](#resume-recovery). Do not
replay an unknown provider side effect or replace the saved phase with a guess.

<a id="role-output"></a>
## Output is malformed, rejected, blocked, or escalated

| Observation and durable evidence | Safe response / permitted change | Never bypass |
| --- | --- | --- |
| Malformed JSON or required fields; `infra-failure` with output-contract error | Inspect the failed attempt. A handled pause can be resumed after reviewing model suitability or correcting an intentional prompt override. Persistent failures need a sanitized defect report. | Do not hand-convert provider text into an accepted persisted result or pretend a failed phase completed. |
| Pre-write RED/GREEN role-contract rejection | Candidate builds containing [BTN-154](https://github.com/lrburkholder/battalion/issues/241) retain reason, offending paths, attempt and no-write evidence; one bounded correction can occur automatically. Observe it; do not race it with resume. If exhausted, review the durable stop. | This is not a capability/scope violation waiver. Prohibited candidates must not be written, and correction still counts against budget. |
| Valid `blocked` or `escalated` result / `role-escalation` | [BTN-133](https://github.com/lrburkholder/battalion/issues/207) candidate evidence includes a typed reason such as insufficient scope or an architecture decision. Supply the missing human decision through approved intervention/resolution controls. | Non-action is not malformed JSON or successful RED/GREEN completion. Do not invent requirements, widen authority, or skip review to make progress. |
| Refactorer valid `no-change` | Inspect its reason and the subsequent Reviewer checkpoint. No repair is needed merely because no files changed. | Do not force a cosmetic mutation or skip refactor review. |

<a id="human-checkpoints"></a>
## A manual checkpoint or budget limit paused the Run

For `manual-checkpoint`, the log retains the phase and the prior execution
evidence. Review the plan/artifacts and approve only the intended continuation.
The `driver` checkpoint is after Architect and before Driver RED. A meaningful
resolution is durable; removing checkpoints from saved state is not recovery.

For `budget-exceeded`, inspect saved `used`/`limit`, attempts, and known/unknown
costs. This budget counts execution turns, not a provider spending cap. A human
may authorize continuation, but current resume does **not** reset or replace
the persisted budget; `resume --budget` does not exist. Changing the YAML
budget does not raise an existing Run's saved limit. If continuation pauses
again at that limit, stop and request a reviewed continuation plan; do not
loop resume, zero the counter, or edit state. A newly scoped Run with its own
approved budget requires workspace/specification review and all normal gates.

For either condition, preserve the interrupt and human decision history. Use
the [resume procedure](#resume-recovery) only when the operator has approved the
next step; an interrupt is not an error to suppress.

<a id="authority-stop"></a>
## A write or authority change was blocked

`out-of-scope-write` retains the error and declared scope in the Run; inspect
artifact evidence and workspace too. A denied write does not prove that no
earlier valid write occurred. `role-definition-edit` records a role/scope
change requiring human architectural review. Invalid path configuration may
instead fail before a Run or resume attempt is recorded.

Stop and compare the intended task, approved architecture, exact project path,
and rejected path. Correct a mistaken project selection or an invalid *new-Run*
configuration using narrow project-relative roots. An existing Run retains its
saved write scope; changing YAML is not a supported way to widen it on resume.
If the task actually needs more authority, seek an explicit architectural
decision and a properly admitted new Run. Do not use `./`, absolute paths,
traversal, symlink/junction escapes, Reviewer write tools, or direct JSON edits
to bypass the boundary. Resume a handled stop only after review confirms the
remaining work fits existing authority; it may stop again if the cause remains.

<a id="reviewer-tests"></a>
## Reviewer reports failures, cannot collect tests, or hangs

For artifacts containing BTN-164, inspect `test_execution` on the Reviewer
attempt in JSON status or the desktop Inspector. All non-verdict outcomes below
pause through `infra-failure` without an LLM judgment. Fix the harness/environment
under human review, then resume the **same Reviewer checkpoint**. Preserve the
original process evidence; never relabel it as a pass or expected RED.

| Classification / symptom | Meaning and safe next action |
| --- | --- |
| `test-failure` | Collected-test failure with no harness errors is eligible RED evidence. In GREEN/refactor it is a rejection: inspect assertions and recorded review cause; let the required correction/review loop operate. Human changes must preserve the approved spec and RED/GREEN separation. |
| `pass` | Collected tests passed. Required for GREEN/refactor; unexpected in RED and may produce a rejection. Check that the test actually demonstrates the missing behavior. Do not weaken tests to manufacture acceptance. |
| `same-root-cause-twice` | Saved Reviewer rejection history repeats a cause at a checkpoint. Human analysis/intervention is required before resume; do not clear history or retry counters. |
| `collection-usage-internal-error` | Includes syntax/import/collection, setup/teardown, usage and internal pytest errors. Repair missing project dependencies, discovery, imports, or harness configuration. An importable stub can establish a real assertion failure in RED. A nonzero exit alone is not RED evidence. |
| `no-tests-collected` | Zero collected tests is neither a pass nor valid RED. Check project path, test filenames/discovery settings, and admitted test files. Do not add dummy tests merely to clear the gate. |
| `timeout` | Inspect duration, configured limit, output and process-tree cleanup. Diagnose hanging tests; a reviewed timeout change is allowed in `reviewer_test_timeout_seconds` (default 300, greater than 0 and at most 3600). It applies on resume. Do not disable the bound or retry while cleanup is uncertain. |
| `cancellation` | Execution was interrupted; neither acceptance nor a completed failing test is established. Confirm child-process cleanup and inspect durable recovery before resuming. |
| `process-launch-failure` | The test process could not start. Check the recorded command/interpreter and installed project dependencies. For the frozen worker, see the BTN-132 limitation above; do not silently substitute CLI execution to accept the ZIP. |
| `malformed-output`, `invalid-pytest-outcome` | Missing/malformed JUnit or contradictory/unsupported exit evidence. Preserve bounded output; investigate/report the harness failure. Never infer success from a partial transcript. |

Reviewer tests run in a disposable snapshot. It admits tracked and nonignored
untracked project files while excluding generated builds, virtual environments,
caches, Battalion state and VCS metadata; outside-project links are excluded.
If a required file is missing, inspect admission/ignore rules rather than
copying the entire machine into the snapshot. Tests can execute code: the
snapshot is **not an OS security sandbox**. Reviewer still has no project write
tools. Run only trusted, disposable UAT inputs.

<a id="resume-recovery"></a>
## Resume was interrupted or an intervention seems missing

First verify artifact applicability above. On pre-BTN-165 or unknown builds,
do not promise replay after a crash: preserve state and seek reviewed recovery.
On a candidate containing BTN-165, inspect `Recovery:` and the saved
`graph_progress`/`resume_intent`, not just a phase label or stale worker flag.

| Durable evidence | Action after confirming no active execution |
| --- | --- |
| Unresolved HumanInterrupt | Review the reason, confirm Actor and resolution, then authorize the ordinary resume below. |
| `interrupted-before-attempt` or `attempt-created`, recoverable | The saved authorization/attempt identity can be reused before generation. Repeat the original Actor, resolution, and action ID if supplied. Do not queue a duplicate intervention. |
| `attempt-started` without a durable outcome, terminal | Provider calls or workspace writes may have happened. Stop replay, back up evidence, inspect actual changes, and start a new Run only from the reviewed workspace/specification. Never force the old attempt to run again. |
| `attempt-completed` or `outcome-checkpointed`, recoverable | Continue from the stored successor, including required Reviewer checkpoints. Do not rerun the completed node or choose a successor by editing JSON. |
| Recursion limit or unexpected graph exception | A BTN-165 build retains the latest durable checkpoint. Follow its recovery disposition; missing evidence is not permission to guess. Preserve the error and report it. |

For a new, reviewed resume decision, choose one action ID and retain it with
the exact resolution and Actor ID. The selected local human Actor is the CLI
default; the desktop shows the Actor before **Resolve and resume**. You may
pass the recorded Actor UUID via `--actor-id` when retrying that decision.

<!-- check:resume -->
```powershell
$ActionId = [guid]::NewGuid().ToString()
$Resolution = Read-Host 'Reviewed resolution authorizing this continuation'
& $Python -m battalion resume $RunId --resolution $Resolution --action-id $ActionId
& $Python -m battalion status $RunId --human
```

After a crash, **do not regenerate** `$ActionId` or change `$Resolution`: rerun
only the resume command with the original values after inspection says it is
safe. An explicit ID deduplicates even after completion; a later new interrupt
requires a new human decision and new ID. Reusing an ID with a different Actor
or resolution is rejected. Without an explicit ID, a pending intent is reused,
but a later newly paused Run is a new decision.

Interventions are tied to an exact receiving-attempt identity. In desktop,
inspect queued/delivered state and the receiving attempt before trying again.
Only approved Correction/Design decision targets are available; Reviewer is
not an intervention target, and active-worker writes are rejected. A delivered
action with an uncertain started attempt does not justify replay. A conflicting
Actor/action ID requires the original decision evidence, not registry edits.
None of these guarantees rolls back file writes or ensures exactly-once
provider calls. See [operator workflows](ui/workflow.md) for action boundaries.

<a id="worker-recovery"></a>
## Desktop stopped updating or the worker exited

Closing the UI does not necessarily stop its detached worker. Reopen the same
verified desktop with the same absolute project path, select the same UUID,
and refresh authoritative state. This reloads durable evidence; missing live
token text need not be replayed. Do not start a second worker/CLI resume while
the first may still be executing.

| Worker observation | Interpretation and safe action |
| --- | --- |
| `starting`, `running`, `cancelling` | Active supervision states; wait/refresh and inspect the exact process if necessary. Do not duplicate work. `cancelling` is not proof that all work stopped. |
| Old timestamp or apparently stale UI | There is no heartbeat-age recovery guarantee. Metadata/PID existence alone cannot establish a safe restart (including a reused PID or `starting` record with no PID). Refresh and verify the actual process and Run; unresolved ambiguity needs operator investigation. Do not delete metadata to clear the active guard. |
| `crashed` | Refresh reconciles an active record whose recorded PID no longer exists to a crash (or cancelled if cancellation was requested). Saved state may exist, but only Run recovery evidence determines replay safety. |
| `failed` or `cancelled` | Worker-level failure/cancellation is not a Run verdict. Inspect error, interrupt and attempt outcome, then follow resume recovery; a worker restart is not an unconditional repair. |
| `succeeded` | The worker operation returned, possibly at a HumanInterrupt. Inspect Run status before claiming completion. |
| Run recovery `recoverable` | Once no process is active, use **Resolve and resume** if enabled for this candidate and inspect the saved decision/target. CLI resume is an alternative operation on the same state, never concurrent. |
| Run recovery `terminal`, or Run `done` | Do not replay the Run. Keep terminal evidence in History; review ambiguous workspace changes before any new Run. |

There is no public `battalion worker restart` CLI command. Do not run a worker
EXE manually with fabricated stdin, kill all Python processes, or delete locks/
worker records. If an exact worker cannot be safely identified, stop and report
the diagnostic bundle. A frozen Reviewer failure needs an identified corrected
ZIP; reconnecting cannot repair the bundled runtime.

<a id="state-backup"></a>
## State is missing, unreadable, or needs a recovery backup

Project-local `.battalion/state/<RUN_UUID>.json` contains the versioned Run,
interrupt/human-action history, execution record, and available recovery
checkpoints. `.battalion/workers/<RUN_UUID>.json` records worker supervision;
`.battalion/project.json`, `runs.json`, and `actors.json` hold project/catalog/
Actor identity. Intel, integration effects, and optional traces are separate
project-local evidence; the Run file is not a complete project backup.

For Run-not-found, verify project path and exact ID against the transcript or
desktop catalog. For malformed JSON, unsupported schema, permission/I/O error,
or unreadable Actor registry, retain the original bytes and error. Use the
matching artifact, correct legitimate filesystem access through the approved
process, or request maintainer-assisted recovery. Do not replace corrupt state
with a new empty record, downgrade/migrate it by hand, or treat catalog metadata
as a replacement for canonical Run evidence.

Before recovery work, wait until **all project writers are inactive**, including
foreground CLI, detached workers, and other clients. Preserve relevant workspace
changes separately under normal version-control/backup procedures. Then make a
narrow, private copy of the affected Run and identity files. This deliberately
excludes unrelated Runs, traces, credentials, integration ledgers and Intel; it
is diagnostic preservation, not an automatic restore package.

<!-- check:backup -->
```powershell
$BackupParent = (Resolve-Path -LiteralPath (Read-Host 'Private backup directory outside this project')).Path
$ProjectRoot = (Resolve-Path -LiteralPath $Project).Path.TrimEnd([char[]]'\/')
if ($BackupParent -eq $ProjectRoot -or $BackupParent.StartsWith($ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose a private backup directory outside the project'
}
$Backup = Join-Path $BackupParent ('battalion-recovery-' + [guid]::NewGuid().ToString())
$StateRelative = 'state/' + $RunId + '.json'
$StateFile = Join-Path $Project ('.battalion/' + $StateRelative)
if (!(Test-Path -LiteralPath $StateFile -PathType Leaf)) { throw 'Run state missing; record the error instead of creating replacement state' }
foreach ($Relative in @($StateRelative, ('workers/' + $RunId + '.json'), 'project.json', 'runs.json', 'actors.json')) {
    $Source = Join-Path $Project ('.battalion/' + $Relative)
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        $Destination = Join-Path $Backup $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
}
Get-FileHash -LiteralPath (Join-Path $Backup $StateRelative) -Algorithm SHA256
```

Keep that backup access-controlled. It may contain private specifications,
error text, and source artifacts. Do not restore it over live state: a restore
can rewind an already-applied write or external effect. Ordinary repair never
requires hand-editing persisted JSON, clearing interrupts, removing Actor
authority, or broadening scopes. If evidence cannot establish safety, stop.

## Report a reproducible finding

Record the symptom/anchor, artifact and guide identities, sanitized diagnostic
excerpt, expected versus actual behavior, last safe checkpoint, and whether
retry was attempted. Keep raw evidence private. Submit through the project's
approved issue/reporting process; do not upload a whole `.battalion` directory.
The prepared [CLI UAT](uat/cli.md) and [desktop UAT](uat/desktop.md) paths use this
guide alone. Record reviewer/date/script revision and approval before formal
execution; final live acceptance follows BTN-173 under BTN-129/BTN-132.
