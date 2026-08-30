# Getting Started

Install Battalion from an identified artifact, configure models, then run a
small ticket in a disposable project. No Battalion source checkout or developer
installation is needed. These instructions use **Windows PowerShell 5.1 or
PowerShell 7 on Windows**. The desktop distribution targets Windows x64 only;
this guide makes no macOS or Linux desktop support claim.

If a step fails, stop and use [Troubleshooting and recovery](troubleshooting.md).
It covers diagnostic collection, installation/provider errors, Reviewer failures,
worker reconnect, safe resume, and private state backups. Check its candidate
applicability before relying on crash recovery.

## 1. Choose an available artifact

**There is no public GitHub Release as of 2026-08-30.** A package version in
source, a green build, or a merge does not mean a release is available.

| Situation | What to install |
| --- | --- |
| No public release and no candidate supplied | Stop here. There is no end-user download to install yet. |
| Named candidate for UAT | Obtain the wheel, Windows desktop ZIP if needed, SHA-256 files, and provenance metadata from the candidate handoff. Record its build/run URL and exact source commit. A candidate is not a supported release. |
| An available published release | Open [GitHub Releases](https://github.com/lrburkholder/battalion/releases), select an explicit tag, and download that release's assets, checksum files, and metadata. Read its limitations before installation. |

The CLI asset is a wheel such as `battalion-<VERSION>-py3-none-any.whl`.
The desktop asset is `battalion-desktop-windows-x64-v<VERSION>.zip`.
Release checksum files are `battalion-python-SHA256SUMS.txt` and
`battalion-desktop-windows-x64-SHA256SUMS.txt`; provenance files are
`python-release-metadata.json` and `desktop-release-metadata.json`.
Candidates must supply equivalent filename/checksum/version/source-commit
evidence; do not invent a release tag for an untagged candidate. If that
evidence is missing, stop and record a packaging defect. The current release
path does not publish to PyPI. Do not substitute a similarly named registry
package. See [Releases and distribution](release.md).

Use the guide revision supplied with the candidate for preparation. Final
release-gate UAT follows the published guide on the main-based candidate after
[BTN-173](https://github.com/lrburkholder/battalion/issues/271). Adding this file
on a branch does not mean GitHub Pages has deployed it.

## 2. Verify and install the CLI wheel

Prerequisites: Python 3.11 or newer with `venv` and `pip`, Git, disk space for a
fresh virtual environment, and package-index access for Python dependencies
(or an organization-provided offline dependency cache). Install these through
your organization's approved process. No Rust, Node, Qt, Nuitka, or Battalion
developer extra is required for the CLI. Pytest is separately needed to review
the example project's Python tests; it is not a core wheel dependency.

Open a fresh PowerShell session outside any Battalion checkout. Run blocks in
order in the same session. At each path prompt, paste an absolute path without
surrounding quotes. Do not literally enter angle-bracket placeholders.

<!-- check:artifact-input -->
```powershell
$Wheel = (Resolve-Path -LiteralPath (Read-Host 'Wheel path')).Path
$WheelSums = (Resolve-Path -LiteralPath (Read-Host 'Python SHA256SUMS path')).Path
$PythonMetadata = (Resolve-Path -LiteralPath (Read-Host 'Python metadata JSON path')).Path
$Provenance = Get-Content -LiteralPath $PythonMetadata -Raw | ConvertFrom-Json
$Provenance | Format-List
```

Check `version` and `revision` against the candidate handoff or selected release
tag/commit. A filename alone is insufficient. Verify the wheel before installing:

<!-- check:checksum -->
```powershell
function Assert-ArtifactHash([string]$Artifact, [string]$ChecksumFile) {
    $Leaf = Split-Path -Leaf $Artifact
    $Pattern = '^([0-9a-fA-F]{64}) [ *]' + [regex]::Escape($Leaf) + '$'
    $Lines = @(Get-Content -LiteralPath $ChecksumFile | Where-Object { $_ -match $Pattern })
    if ($Lines.Count -ne 1) { throw "Expected exactly one checksum for $Leaf" }
    $Expected = ($Lines[0] -split ' ', 2)[0]
    $Actual = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash
    if ($Actual -ne $Expected) { throw "SHA-256 mismatch: $Leaf" }
    Write-Output "Verified SHA-256: $Leaf $Actual"
}
Assert-ArtifactHash $Wheel $WheelSums
```

Checksums detect changed bytes; they do not authenticate an untrusted download.
Use metadata and checksum files from the same trusted handoff/release.

Create a new directory and environment. The explicit Python path avoids needing
to activate a PowerShell script or change your execution policy. If any command
fails, stop; do not continue with a different Python installation accidentally.

<!-- check:install -->
```powershell
python --version
git --version
if ($env:PYTHONPATH) { throw 'Use a clean shell without PYTHONPATH before installing' }
$Lab = Join-Path $env:TEMP ('battalion-first-run-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $Lab | Out-Null
Set-Location -LiteralPath $Lab
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$Python = Join-Path $Lab '.venv\Scripts\python.exe'
& $Python -m pip install $Wheel 'pytest>=8.0'
if ($LASTEXITCODE -ne 0) { throw 'Wheel installation failed' }
& $Python -I -c "import battalion, sys; from importlib.metadata import version; print(version('battalion')); print(battalion.__file__); print(sys.prefix)"
& $Python -m battalion --help
& $Python -m battalion.prompts.smoke
```

The reported version must match the provenance. The module path must be under
this new environment's `Lib\site-packages`, not a source checkout. Help must
list `run`, `resume`, `status`, and `setup`; the smoke command must load every
packaged role prompt without credentials. Missing prompts are a packaging
defect, not a reason to download source files or use `--prompts-dir`.

## 3. Prepare a disposable project

Keep the virtual environment outside the project so Reviewer does not copy it.
Git need not have a remote or a commit for this example. The test convention is
`python -m pytest -q` in a disposable project snapshot, using the CLI's Python
environment. Project dependencies must be installed there too.

<!-- check:project -->
```powershell
$Project = Join-Path $Lab ('hello-project-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $Project | Out-Null
Set-Location -LiteralPath $Project
git init
New-Item -ItemType Directory -Path 'src' | Out-Null
@'
.battalion/
battalion.config.yaml
__pycache__/
.pytest_cache/
.env
'@ | Set-Content -LiteralPath '.gitignore' -Encoding UTF8
@'
def greet(name: str) -> str:
    raise NotImplementedError
'@ | Set-Content -LiteralPath 'src/greeting.py' -Encoding UTF8
@'
# Greeting ticket
Implement src/greeting.py: greet(name: str) returns exactly "Hello, {name}!".
During RED, create src/test_greeting.py with tests for Ada and an empty name.
The existing greet stub must fail those assertions before GREEN implements it.
Use pytest and no additional dependencies. Keep the implementation small.
Architect writes plan.md. Do not modify configuration or this specification.
'@ | Set-Content -LiteralPath 'ticket.md' -Encoding UTF8
```

The default writing root is `src/`: RED writes tests, GREEN writes production
code, and Refactorer works on admitted GREEN artifacts. Reviewer has no project
write tools. Starting with an importable stub makes RED a test failure rather
than a collection failure. A project with no collected tests is not a pass.
Repeating this section creates a new project without overwriting a prior Run;
run setup again in that new directory before starting another scenario.

## 4. Choose models and validate configuration (live provider step)

Choose model identifiers for all four roles. Driver and Reviewer must use
different identifiers. Use explicit provider/model identifiers recognized by
the installed LiteLLM version. A recognized name or successful ping is not a
promise that a provider/model meets Battalion's role-output requirements.

- **Local inference:** install/start your chosen compatible runtime and obtain
  the selected models yourself before setup. Local-looking provider names do
  not prove the endpoint is local, private, offline, or zero cost. Check the
  actual runtime/endpoint configuration. Battalion does not install models.
- **Remote inference:** arrange provider access and spending limits first.
  Supply the provider's expected API-key environment variable to this process
  through your approved secret manager or shell credential procedure. Setup
  reports the required variable name when absent. Do not paste secrets into
  model identifiers, commands saved in transcripts, `battalion.config.yaml`,
  specification files, or Git. Merely creating a `.env` file does not load it.
  The model setup path reads environment variables; integration keyring
  references are not a substitute for that model credential path.

Remote models may receive the specification, admitted project content, role
prompts, and execution context. Use only disposable, non-sensitive content
here. Connectivity validation makes a real completion and may incur charges.
The current setup checks one selected model per provider, not all role models,
and is not a full capability test. Inspect every selected role afterward.

<!-- check:setup -->
```powershell
$ArchitectModel = Read-Host 'Architect provider/model'
$DriverModel = Read-Host 'Driver provider/model'
$ReviewerModel = Read-Host 'Reviewer provider/model (different from Driver)'
$RefactorerModel = Read-Host 'Refactorer provider/model'
& $Python -m battalion setup --model-architect $ArchitectModel --model-driver $DriverModel --model-reviewer $ReviewerModel --model-refactorer $RefactorerModel --validate
```

Expected: connectivity succeeds and `battalion.config.yaml` is written in the
project with the four selected model identifiers and no API keys. Do not
accept implicit defaults without reviewing them. `--no-validate` deliberately
skips live connectivity; it does not establish readiness or skip credential
and diversity checks. Setup is currently CLI-based, including for desktop users.

## 5. Run, inspect, and resume (live provider steps)

This ticket ID is only a local example; it does not create a GitHub Issue. The
manual `driver` checkpoint pauses **after Architect and before Driver RED**.
The budget counts turns rather than dollars; it is not a provider spending cap.

<!-- check:run -->
```powershell
& $Python -m battalion run BTN-HELLO-1 --spec ticket.md --checkpoint driver --budget 20
```

Copy the canonical Run UUID printed in the command output, then inspect it:

<!-- check:status -->
```powershell
$RunId = Read-Host 'Paste the printed Run UUID'
[guid]::Parse($RunId) | Out-Null
& $Python -m battalion status $RunId --human
```

Expected: `Status: awaiting-human`, a `manual-checkpoint` interrupt, and an
Architect `plan.md` to review. Read that plan and confirm its paths and scope
before authorizing continuation. If an earlier provider or scope failure occurs,
inspect the reason instead of treating it as the intended checkpoint. Never
edit saved JSON to force progress or expand authority to clear a failure.

<!-- check:resume -->
```powershell
Get-Content -LiteralPath 'plan.md'
& $Python -m battalion resume $RunId --resolution 'Reviewed the greeting plan and approved continuation within src/'
& $Python -m battalion status $RunId --human --costs
& $Python -m pytest -q
```

Expected final state: `done`, with passing tests and the same Run UUID. The
execution record must show Architect, Driver RED, Reviewer RED, Driver GREEN,
Reviewer GREEN, Refactorer, and Reviewer refactor in order, plus the durable
human resolution. A valid Refactorer no-change result still requires review.
Another interrupt is not completion: inspect and address it before deciding
whether to resume. A successful process exit alone does not prove `done`.

Status and resume must be run from the same project. New runs use UUIDs, not
identifiers such as `run-BTN-HELLO-1`. Repeating `run` creates another Run; it
does not resume the previous one. For structured evidence:

<!-- check:evidence -->
```powershell
& $Python -m battalion status $RunId
```

Keep the transcript, artifact/provenance evidence, configuration with secrets
removed, and `.battalion/state/<RUN_UUID>.json` for local review. Known costs and
unknown costs remain distinct. Optional `--trace-output` captures raw provider
text and may contain sensitive data; it is unnecessary for this first run.

## 6. Open the Windows desktop ZIP

Complete CLI installation and project setup first. The ZIP contains the Qt UI
and its worker runtime, but no first-run model/configuration wizard. The CLI
wheel and desktop ZIP must come from the same candidate/release version and
source commit. Keep both environments; a frozen worker does not use the CLI
virtual environment automatically.

Current packaging has no configured code-signing step or signed installer.
SmartScreen or organizational policy may block an unfamiliar executable.
Checksums are not signatures. Record the exact warning and seek your
organization's approval if needed; do not disable protection or assume that an
unverified executable is safe.

With the earlier checksum function still defined:

<!-- check:desktop-install -->
```powershell
$DesktopZip = (Resolve-Path -LiteralPath (Read-Host 'Desktop ZIP path')).Path
$DesktopSums = (Resolve-Path -LiteralPath (Read-Host 'Desktop SHA256SUMS path')).Path
$DesktopMetadata = (Resolve-Path -LiteralPath (Read-Host 'Desktop metadata JSON path')).Path
$DesktopProvenance = Get-Content -LiteralPath $DesktopMetadata -Raw | ConvertFrom-Json
if ($DesktopProvenance.version -ne $Provenance.version -or $DesktopProvenance.revision -ne $Provenance.revision) { throw 'CLI and desktop provenance differ' }
Assert-ArtifactHash $DesktopZip $DesktopSums
$DesktopRoot = Join-Path $Lab 'desktop'
Expand-Archive -LiteralPath $DesktopZip -DestinationPath $DesktopRoot
$DesktopExe = Join-Path $DesktopRoot 'Battalion.dist\Battalion.exe'
$WorkerExe = Join-Path $DesktopRoot 'worker\worker_entry.dist\BattalionWorker.exe'
if (!(Test-Path -LiteralPath $DesktopExe) -or !(Test-Path -LiteralPath $WorkerExe)) { throw 'Incomplete desktop/worker layout' }
& $WorkerExe --smoke-role-prompts
if ($LASTEXITCODE -ne 0) { throw 'Packaged worker prompt check failed' }
```

Preserve all DLLs/data and the sibling directory layout; do not move only the
EXE. Launch from the same shell if the provider needs its environment variables:

<!-- check:desktop-launch -->
```powershell
& $DesktopExe --project $Project
```

The `--project` argument selects the disposable repository. To inspect another
project, close the window and relaunch with that project's absolute path;
the current Project menu provides refresh, not an open-project picker.
**Work** shows active/actionable Runs; **History** shows terminal Runs, including
the completed CLI example. Select a Run and attempt to inspect the same UUID,
status, model, artifact, and execution evidence. `Ctrl+R` refreshes authoritative
state. For a paused Run, review the Actor and resolution before using **Resolve
and resume**. The UI starts its sibling worker through the shared application
boundary; closing the window is not a promise to stop that worker.

**Known packaged execution limitation:** the current build excludes pytest,
while Reviewer launches `sys.executable -m pytest`. In a frozen worker that
executable is `BattalionWorker.exe`, whose entry point does not accept those
arguments. Installing pytest into the CLI environment does not fix that bundle
boundary. Read-only inspection and prompt smoke success therefore do not prove
desktop completion. Record the first packaged Reviewer result as a release-gate
finding in [desktop UAT](uat/desktop.md); do not silently switch to source mode
and call the ZIP accepted. Remediation/acceptance belongs to BTN-132, not this
onboarding document.

## Validation and next steps

The [CLI UAT script](uat/cli.md) and [desktop UAT script](uat/desktop.md) use this
guide as their documentation-only onboarding pass. Every required undocumented
step is a defect. Their final live acceptance follows BTN-173 and is owned by
BTN-129/BTN-132; automated syntax and smoke checks are not human acceptance.
See [operator workflows](ui/workflow.md) for the existing UI contract and
[contributor guidance](contributing.md) for source development, which is a
separate installation path.
