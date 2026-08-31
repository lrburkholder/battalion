# Troubleshooting and Recovery

Use this guide when Battalion does not install, does not start, pauses
unexpectedly, or cannot safely continue a Run.

The most important rule is simple: **inspect the saved evidence before retrying.**
A retry can call a model again, write files again, or repeat work whose outcome
is uncertain. Do not edit Battalion's saved JSON to make a Run continue.

## Start here

Before changing anything, record:

- the Battalion artifact filename, version, SHA-256, and source commit;
- where the artifact came from;
- your OS and architecture;
- the exact error and approximate time;
- the absolute project path;
- the ticket ID and canonical Run UUID, if a Run was created;
- the current Run status, phase, and last interrupt; and
- the worker status, if the desktop application was involved.

If Battalion failed before creating a Run, say that. Do not invent a Run ID.

These examples use Windows PowerShell. `$Python` must point to the Python from
your installed Battalion CLI environment, **not** to `BattalionWorker.exe`.

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

Always run `status` and `resume` from the same project that owns the Run. New
Runs use UUIDs; do not manufacture an ID such as `run-BTN-*`.

For more detail:

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

A worker record tells you what Battalion last recorded. It is not proof that a
process is still alive. Foreground CLI Runs may not have a worker record at all.

Keep diagnostics local until you have removed credentials, authorization
headers, secret URLs, personal data, and private source content. Raw
`--trace-output` can contain provider text, source code, and secrets; do not
enable it just because a Run failed.

## Find your problem

| What happened | Go to |
| --- | --- |
| Battalion will not install or start | [Installation and startup](#installation-startup) |
| Model setup fails | [Model setup and providers](#setup-provider) |
| The Run says `awaiting-human`, `blocked`, or `failed-infra` | [The Run stopped](#run-stopped) |
| A model returned invalid or blocked output | [Role output problems](#role-output) |
| Battalion paused at a checkpoint or budget limit | [Checkpoints and budget](#human-checkpoints) |
| Battalion blocked a file or role change | [Authority and write-scope stops](#authority-stop) |
| Reviewer cannot run or interpret tests | [Reviewer test problems](#reviewer-tests) |
| A crash happened during resume | [Safe resume and crash recovery](#resume-recovery) |
| Desktop stopped updating or its worker exited | [Desktop worker recovery](#worker-recovery) |
| Run state is missing or corrupt | [State and backups](#state-backup) |

## Check which build you are using

Recovery behavior has changed during pre-1.0 development. Match your artifact's
**source commit**, not just its version string, to the UAT candidate or release
handoff.

Important current boundaries:

| Build | What you need to know |
| --- | --- |
| Before BTN-164 | Reviewer test results are not reliable enough for release acceptance: nonzero pytest exits could be treated as RED and there was no bounded timeout. |
| BTN-164 or later | Reviewer records classified, bounded test execution. This still does not make arbitrary tests safe to run. |
| Before BTN-165 | A crash during resume may lose or consume authorization/progress. Do not blindly retry. |
| BTN-165 or later | Battalion saves resume intent and graph checkpoints, but an attempt that started without saving an outcome can still be unsafe to replay. |
| BTN-129 outstanding case | An empty Architect response can still leave recovery unsafe. Preserve the evidence and inspect the workspace. |
| BTN-132 current desktop package | The frozen worker cannot currently execute Reviewer's pytest command correctly. Use a corrected ZIP for desktop execution acceptance. |

For formal UAT, use the exact candidate handoff and guide revision. Documentation
on another branch does not prove that the candidate contains the behavior it
describes.

<a id="installation-startup"></a>
## Battalion will not install or start

Installation failures normally happen before a Run exists. Keep the installer
or shell output and leave existing project state alone.

| Problem | Safe next step | Do not |
| --- | --- | --- |
| Python is too old or `venv`/`pip` is missing | Install an approved Python 3.11+ environment and repeat the verified-wheel steps in [Getting Started](getting-started.md). | Switch interpreters halfway through an installation. |
| Desktop is on an unsupported platform | The current desktop package targets Windows x64. Use a matching approved artifact or stop. | Assume the Windows ZIP works on macOS, Linux, or another architecture. |
| Dependency installation fails | Save the pip error, fix approved package-index/proxy/cache access, and retry in a fresh environment. | Disable TLS verification or install a similarly named package. |
| Checksum or provenance does not match | Stop. Obtain the artifact again from the trusted source and verify filename, hash, version, and source commit. | Recompute the expected checksum from the suspect file or mix builds. |
| PowerShell will not activate the virtual environment | Use the explicit `$Python` path from Getting Started. Activation is optional. | Weaken machine execution policy just to activate the environment. |
| SmartScreen or organizational policy blocks the desktop executable | Keep the warning and provenance and follow your organization's approval process. | Disable protection. A checksum is not a code signature. |
| Packaged prompts are missing or invalid | Run the prompt smoke test below. Replace/report an incomplete artifact. | Copy prompts from a source checkout and call the package valid. |
| Desktop EXE or worker is missing | Re-extract the entire verified ZIP and preserve its directory structure. | Move only the EXE or substitute another worker. |

<!-- check:prompt-smoke -->
```powershell
& $Python -m battalion --help
& $Python -m battalion.prompts.smoke
```

For the desktop package, run the worker's `--smoke-role-prompts` check from the
path shown in Getting Started. A successful prompt smoke check proves only that
the prompts are packaged; it does not prove provider connectivity or a complete
desktop Run.

<a id="setup-provider"></a>
## Model setup or provider validation fails

Setup normally fails before graph execution. Preserve the error and any existing
configuration.

| Problem | Safe next step | Important boundary |
| --- | --- | --- |
| `MissingApiKey` | Set the provider's named environment variable using your approved secret process, then rerun setup. | A `.env` file is not loaded automatically. Never put the key in YAML, model names, tickets, or transcripts. |
| Connection refused, endpoint unavailable, or timeout | Confirm the intended runtime is running, the model exists, and approved network access works. | A local-looking model name does not prove that the endpoint is local, private, or free. |
| Unknown provider/model | Check the exact provider/model identifier supported by your installed runtime and LiteLLM version. | Credentials do not fix an invalid model name. |
| Authentication, quota, or capability error | Fix the account, quota, endpoint, or model selection and retry validation. | Validation performs a real request and may cost money. |
| Driver and Reviewer use the same model | Select different model identifiers and rerun setup. | Do not bypass Battalion's model-diversity check. |

`--no-validate` skips the live connectivity request. It does not prove that the
models are usable, and it does not bypass credential or diversity checks.

<a id="run-stopped"></a>
## The Run stopped

First inspect the saved status. A stopped Run is not necessarily a crash.

- `awaiting-human` usually means Battalion deliberately created a durable
  HumanInterrupt and is waiting for a decision.
- `blocked` can be a valid role result saying that the role needs information or
  authority it does not have.
- `failed-infra` means execution failed and Battalion could not complete the
  phase normally.
- `done` is the completion state. A process exit or worker status of `succeeded`
  does not replace it.

Before retrying anything, make sure no CLI process or desktop worker is still
executing the Run. `resume` is an authorization to continue work; it can call
models and write files. It is not a diagnostic command.

<a id="infra-failure"></a>
### Infrastructure or provider failure

For `infra-failure`, inspect the saved error and execution attempt. The actual
cause may be a provider failure, malformed role output, or Reviewer test-harness
failure.

If Battalion saved a handled pause, fix the underlying credential, endpoint,
quota, model, or harness problem and then resume from the saved target. Previous
provider calls may already have consumed tokens or money; Battalion does not
promise exactly-once provider calls.

If the process disappeared without saving a clear outcome, use
[Safe resume and crash recovery](#resume-recovery). Do not guess which phase
should run next.

<a id="role-output"></a>
## Role output is malformed, blocked, or escalated

| What you see | What it means / what to do |
| --- | --- |
| Malformed JSON or missing required fields | Inspect the failed attempt. If Battalion created a handled pause, review model suitability or an intentional prompt override before resuming. Do not manually turn provider text into an accepted result. |
| RED/GREEN role-contract rejection before a write | Newer builds retain the rejected candidate, reason, offending paths, and proof that the prohibited write did not occur. Battalion may make one bounded automatic correction. Let that correction finish before intervening. |
| `blocked`, `escalated`, or `role-escalation` | The role is explicitly saying that it cannot safely continue. Read the typed reason and supply the missing human decision through Battalion's normal controls. Do not invent requirements or widen scope just to make it proceed. |
| Refactorer `no-change` | This can be valid. Read the reason and allow the required Reviewer checkpoint to run. Do not force a cosmetic change. |

An automatic role-contract correction does not waive write-scope rules and still
uses the Run's normal budget.

<a id="human-checkpoints"></a>
## A checkpoint or budget limit paused the Run

### Manual checkpoint

A `manual-checkpoint` is intentional. Review the artifacts and approve only the
next step you actually intend. For example, a `driver` checkpoint occurs after
Architect and before Driver RED.

Your resolution becomes durable Run evidence. Do not remove the checkpoint from
saved state to make the Run continue.

### Budget exceeded

Battalion's Run budget counts execution turns, **not provider dollars**. Inspect
the saved `used` and `limit` values, attempts, and known/unknown costs before
deciding whether to continue.

The current `resume` command does not reset an existing Run's budget. Changing
YAML also does not increase the limit already stored in that Run. If the Run
immediately reaches the same limit again, stop and reassess instead of repeatedly
calling resume or editing the counter.

<a id="authority-stop"></a>
## Battalion blocked a write or authority change

`out-of-scope-write` means a role tried to write outside the authority granted
to it. `role-definition-edit` means a change would affect Battalion's role
behavior and requires human architectural review.

Compare:

1. the task you intended,
2. the approved architecture,
3. the actual project path,
4. the Run's saved write scope, and
5. the path Battalion rejected.

If the project or new-Run configuration is wrong, correct it with narrow,
project-relative paths. An existing Run keeps its saved authority; changing
YAML is not a supported way to widen it during resume.

If the task genuinely needs more authority, make that an explicit architectural
decision and admit a properly scoped new Run. Do not use `./`, absolute paths,
parent traversal, symlink/junction escapes, Reviewer write tools, or hand-edited
state to bypass the boundary.

<a id="reviewer-tests"></a>
## Reviewer test problems

On builds containing BTN-164, Reviewer records a `test_execution` result. If the
result is not a valid RED/GREEN/refactor verdict, Battalion pauses with an
infrastructure failure instead of asking the model to guess.

| Classification | Meaning |
| --- | --- |
| `test-failure` | Tests were collected and failed without a harness error. This can be valid RED evidence. During GREEN/refactor it is a rejection that must go through the normal correction loop. |
| `pass` | Collected tests passed. This is required for GREEN/refactor. During RED, an unexpected pass means the test may not demonstrate the missing behavior. |
| `same-root-cause-twice` | Reviewer has rejected the same cause twice at the checkpoint. Human analysis is required. Do not clear rejection history. |
| `collection-usage-internal-error` | Pytest could not correctly collect or execute the suite because of imports, syntax, setup, usage, or internal errors. Fix the project/harness problem; a nonzero exit alone is not RED. |
| `no-tests-collected` | Zero tests were collected. This is neither a pass nor valid RED evidence. Check paths and pytest discovery. |
| `timeout` | Tests exceeded the configured timeout. Diagnose the hang before retrying. The timeout can be reviewed and changed within the supported 1–3600 second range. |
| `cancellation` | Test execution was interrupted. No pass/fail conclusion is established. |
| `process-launch-failure` | Pytest could not start. Check the interpreter/runtime and dependencies. The current frozen desktop worker has a known BTN-132 limitation here. |
| `malformed-output` / `invalid-pytest-outcome` | The recorded pytest evidence is incomplete or contradictory. Preserve it and investigate the harness; do not infer a verdict from partial output. |

Reviewer runs tests in a disposable project snapshot. The snapshot excludes
common build outputs, virtual environments, caches, Battalion state, VCS
metadata, and links outside the project. It is **not an operating-system security
sandbox**: tests can execute code. Use trusted inputs.

Reviewer still has no project write tools.

<a id="resume-recovery"></a>
## Safe resume and crash recovery

This is the area where guessing is most dangerous.

On builds before BTN-165, do not assume that a crashed resume can be replayed.
Preserve the evidence and use reviewed recovery.

On BTN-165 or later, inspect the Run's `Recovery:` result and its saved progress,
not just the phase name or worker status.

| Saved evidence | Safe action |
| --- | --- |
| Unresolved HumanInterrupt | Review the reason and authorize an ordinary resume when ready. |
| `interrupted-before-attempt` or `attempt-created`, recoverable | Battalion saved enough information to reuse the decision before model generation. Retry with the same Actor, resolution, and action ID if one was used. |
| `attempt-started` with no durable outcome, terminal | **Do not replay it.** A provider call or file write may already have happened. Back up the evidence, inspect the workspace, and start a new Run only from a reviewed state. |
| `attempt-completed` or `outcome-checkpointed`, recoverable | Continue from Battalion's stored successor. Do not rerun the completed node. |
| Recursion limit or unexpected graph exception | Follow the saved recovery disposition. If evidence is missing, stop rather than guessing. |

For a new reviewed resume decision, you can provide an action ID:

<!-- check:resume -->
```powershell
$ActionId = [guid]::NewGuid().ToString()
$Resolution = Read-Host 'Reviewed resolution authorizing this continuation'
& $Python -m battalion resume $RunId --resolution $Resolution --action-id $ActionId
& $Python -m battalion status $RunId --human
```

If that resume itself crashes, **reuse the same action ID and resolution** only
after the saved recovery evidence says retry is safe. Do not generate a new ID
for the same decision. A later, genuinely new interrupt needs a new decision and
new ID.

These protections deduplicate Battalion's decision handling. They do not roll
back file writes or guarantee exactly-once provider calls.

<a id="worker-recovery"></a>
## Desktop stopped updating or its worker exited

Closing the desktop window does not necessarily stop its detached worker.
Reopen the same verified desktop package with the same project path and refresh
authoritative state (`Ctrl+R`). Do not start a CLI resume or second worker while
the original worker may still be executing.

| Worker state | What to do |
| --- | --- |
| `starting`, `running`, `cancelling` | Treat it as potentially active. Wait, refresh, and identify the actual process before doing anything that could duplicate work. |
| Old timestamp / apparently stale UI | A stale timestamp or PID is not enough to prove that restart is safe. Investigate the actual process and Run. |
| `crashed` | Inspect Run recovery evidence. A worker crash does not tell you whether the last model/file operation is safe to replay. |
| `failed` or `cancelled` | Inspect the Run's error, interrupt, and attempt outcome. Worker status is not the Run verdict. |
| `succeeded` | Inspect Run status. The worker may have successfully returned because Battalion reached a HumanInterrupt. |
| Run recovery is `recoverable` | Once you know no process is active, continue through the normal resolve/resume path. |
| Run recovery is `terminal`, or Run is `done` | Do not replay the Run. Review the workspace before starting any new Run. |

There is no public `battalion worker restart` command. Do not manually launch the
worker EXE with invented input, delete worker metadata to clear a guard, or kill
unrelated Python processes.

The current frozen desktop Reviewer problem requires a corrected desktop
artifact; reconnecting the worker cannot repair that package.

<a id="state-backup"></a>
## Run state is missing, unreadable, or needs a backup

Battalion stores canonical Run state under:

`<project>/.battalion/state/<RUN_UUID>.json`

Related files under `.battalion/` hold worker supervision, project/catalog
identity, and Actors. Intel, traces, and integration evidence can be stored
separately. The Run state file is **not** a complete backup of the project.

If Battalion says a Run is missing, first verify the exact project path and Run
ID. If state is corrupt or unreadable, preserve the original file and error.
Do not replace it with an empty state file, manually migrate it, or treat catalog
metadata as a substitute for canonical Run state.

Before copying recovery evidence, make sure all writers are stopped: foreground
CLI, desktop workers, and other clients. Back up source/workspace changes using
normal version-control or backup procedures separately.

Then make a narrow private copy of the affected Battalion state:

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

Keep this backup private. It may contain specifications, source artifacts, and
error text. Do **not** restore it over live state without reviewed recovery: that
could rewind a write or an external effect that already happened.

If the evidence cannot establish that recovery is safe, stop rather than
forcing the old Run forward.

## Reporting a reproducible problem

A useful report includes:

- the symptom;
- artifact version, SHA-256, and source commit;
- guide/candidate identity;
- a sanitized diagnostic excerpt;
- expected versus actual behavior;
- the last known safe checkpoint; and
- whether you attempted a retry.

Keep raw evidence private. Do not upload an entire `.battalion` directory.

For formal testing, see [CLI UAT](uat/cli.md) and [desktop UAT](uat/desktop.md).
For data sensitivity and retention details, see
[Data handling and trust boundaries](data-handling.md).