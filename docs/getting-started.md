# Getting Started

This guide walks through a first Battalion Run using a small disposable project.
You will:

1. get an identified Battalion build,
2. verify and install it,
3. create a test project,
4. choose models,
5. run Battalion and approve a human checkpoint, and
6. optionally open the Windows desktop application.

You do **not** need a Battalion source checkout or a developer installation.
These instructions use Windows PowerShell 5.1 or PowerShell 7. The current
desktop package targets Windows x64 only.

Before you begin, read [Data handling and trust boundaries](data-handling.md).
If something fails, stop and use [Troubleshooting and recovery](troubleshooting.md)
instead of trying to force the Run forward.

## 1. Get an identified Battalion build

Battalion is currently pre-1.0. **There is no public GitHub Release as of
2026-08-30.** A version number, green build, or merge to `main` does not by
itself mean that a release exists.

| What you have | What to do |
| --- | --- |
| No release and no UAT candidate | Stop. There is no end-user build to install yet. |
| A named UAT candidate | Use the wheel, optional Windows desktop ZIP, checksum files, and provenance metadata supplied with that candidate. |
| A published release | Download the assets, checksums, and metadata for one explicit tag from [GitHub Releases](https://github.com/lrburkholder/battalion/releases). |

For the CLI, expect a wheel named like `battalion-<VERSION>-py3-none-any.whl`.
For the desktop, expect `battalion-desktop-windows-x64-v<VERSION>.zip`.

You should also receive checksum and provenance files. The provenance must tell
you the exact version and source commit. If that information is missing, stop
and report a packaging problem. Do not guess a release tag, mix files from
different builds, or install a similarly named package from PyPI; Battalion is
not currently published there.

For formal UAT, use the guide revision supplied with the candidate. See
[Releases and distribution](release.md) for the full release process.

## 2. Verify and install the CLI

You need:

- Python 3.11 or newer, including `venv` and `pip`
- Git
- enough space for a fresh virtual environment
- access to the Python dependencies, either through a package index or an
  approved offline cache

You do not need Rust, Node, Qt, Nuitka, or Battalion's developer dependencies.

Open a fresh PowerShell window **outside a Battalion source checkout**. Run the
following blocks in order and keep using the same shell.

First, identify the wheel and its provenance files:

<!-- check:artifact-input -->
```powershell
$Wheel = (Resolve-Path -LiteralPath (Read-Host 'Wheel path')).Path
$WheelSums = (Resolve-Path -LiteralPath (Read-Host 'Python SHA256SUMS path')).Path
$PythonMetadata = (Resolve-Path -LiteralPath (Read-Host 'Python metadata JSON path')).Path
$Provenance = Get-Content -LiteralPath $PythonMetadata -Raw | ConvertFrom-Json
$Provenance | Format-List
```

Confirm that `version` and `revision` match the candidate or release you meant
to install. Then verify the wheel's SHA-256 hash:

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

A matching checksum tells you that the bytes match the checksum file. It does
not prove that an untrusted download is authentic, so keep the artifact,
checksum, and metadata together from the same trusted source.

Now create a clean environment and install Battalion:

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

Check the output before continuing:

- the installed version should match the provenance metadata;
- `battalion` should load from the new environment's `site-packages`, not from a
  source checkout;
- help should include `run`, `resume`, `status`, and `setup`; and
- the prompt smoke check should succeed without credentials.

If packaged prompts are missing, treat that as a packaging defect. Do not repair
the installation by copying prompts from the source repository.

## 3. Create a disposable test project

Your first Run should use throwaway, non-sensitive code. Keep the Battalion
virtual environment outside the project so Reviewer does not copy it into its
test snapshot.

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

This example is intentionally simple. Architect may write `plan.md`; Driver RED
may write tests under `src/`; Driver GREEN may implement the code; Refactorer
may work only on admitted GREEN artifacts; Reviewer cannot write project files.

The existing importable stub is important: RED should fail because the behavior
is wrong, not because pytest cannot import or collect the tests. Zero collected
tests is not a valid RED result or a pass.

## 4. Choose models and validate setup

This step contacts your configured model provider and may incur charges. Read
[what Battalion sends to models](data-handling.md#model-context) and the
[credential guidance](data-handling.md#credentials) first.

Battalion needs model identifiers for Architect, Driver, Reviewer, and
Refactorer. **Driver and Reviewer must use different model identifiers.**

For local inference, install and start the runtime and models yourself. A model
name that looks local does not prove that its endpoint is local, private,
offline, or free; verify the actual runtime configuration.

For remote inference, configure the provider's expected API-key environment
variable through your normal secret-management process. Do not put credentials
in model names, `battalion.config.yaml`, tickets, transcripts, or Git. A `.env`
file is not loaded automatically.

Remote providers may receive the specification, admitted project content, role
prompts, and execution context. That is why this first Run uses disposable,
non-sensitive content.

<!-- check:setup -->
```powershell
$ArchitectModel = Read-Host 'Architect provider/model'
$DriverModel = Read-Host 'Driver provider/model'
$ReviewerModel = Read-Host 'Reviewer provider/model (different from Driver)'
$RefactorerModel = Read-Host 'Refactorer provider/model'
& $Python -m battalion setup --model-architect $ArchitectModel --model-driver $DriverModel --model-reviewer $ReviewerModel --model-refactorer $RefactorerModel --validate
```

Setup should create `battalion.config.yaml` containing the four model configurations
and **no API keys**. Review the selected models rather than accepting unexpected
defaults.

Validation performs a real provider request for each distinct effective target
(model, endpoint, credential reference, and request settings).
It checks connectivity; it does not prove that every selected model can
successfully perform its Battalion role. `--no-validate` skips the connectivity
request, but it does not make later Runs offline or prove that setup is ready.

Setup is currently performed through the CLI even when you plan to use the
desktop application.

### Local and custom inference endpoints

BTN-52 adds the following setup support on its implementation branch; use a
candidate containing that change to exercise these options. Existing plain
`provider/model` configurations still work. All inference continues through
LiteLLM; Battalion does not install or start model servers.

Each role can also specify `endpoint_url`, `backend` (an optional server name),
`inference_location` (`local`, `remote`, or `unknown`),
`canonical_model_family`, `api_key_env`, and `keyless`. Existing temperature,
retry, and non-secret `extra_params` settings survive setup. Older
`extra_params.api_base` values migrate to `endpoint_url` when setup saves.

| Server | LiteLLM model prefix | Example base URL |
| --- | --- | --- |
| Ollama chat | `ollama_chat/` | `http://localhost:11434` |
| LM Studio | `lm_studio/` or `openai/` | `http://localhost:1234/v1` |
| vLLM HTTP server | `hosted_vllm/` or `openai/` | `http://localhost:8000/v1` |
| Other OpenAI-compatible HTTP server | `openai/` | Your server's base URL, usually ending in `/v1` |

Use the exact model identifier served by your endpoint after the prefix. The
`vllm/` adapter is distinct from the HTTP-serving `hosted_vllm/` adapter. These
formats follow the LiteLLM documentation for [Ollama](https://docs.litellm.ai/docs/providers/ollama),
[LM Studio](https://docs.litellm.ai/docs/providers/lm_studio),
[vLLM](https://docs.litellm.ai/docs/providers/vllm), and
[OpenAI-compatible endpoints](https://docs.litellm.ai/docs/providers/openai_compatible).

For example, to change Driver to an already-running local server while retaining
the other configured roles, replace `qwen3` with its served identifier and family:

```powershell
& $Python -m battalion setup --model-driver openai/qwen3 `
    --endpoint driver=http://localhost:8000/v1 `
    --inference-location driver=local `
    --canonical-model-family driver=qwen3 `
    --backend driver=workstation
```

Each endpoint option accepts `ROLE=VALUE` and may be repeated for another role.
Interactive setup also prompts for the endpoint, inference location, credential
variable name, and Driver/Reviewer family. YAML can express the same fields:

```yaml
models:
  driver:
    model: openai/qwen3
    endpoint_url: http://localhost:8000/v1
    backend: workstation
    inference_location: local
    canonical_model_family: qwen3
    temperature: 0.0
    max_retries: 2
    extra_params:
      timeout: 30
  reviewer:
    model: ollama_chat/llama3.3
    endpoint_url: http://localhost:11434
    inference_location: local
    canonical_model_family: llama3.3
```

Keep the other roles in your configuration too. Setup preserves additional
configured roles, including Tactician, and checks their targets. A live check
uses the selected endpoint and request settings with a one-token ping; a failed
check leaves the existing file unchanged. `--no-validate` skips network calls
but still checks configuration, diversity, and required credential references.

Loopback endpoints and supported keyless adapters do not require cloud-provider
credentials. Battalion sends a non-secret placeholder where the client requires
a key value, so ambient cloud credentials are not forwarded to keyless servers.
For a server requiring a bearer token, put the token in your environment and use
`--api-key-env driver=LOCAL_INFERENCE_TOKEN`; save only that variable name.
An authenticated custom remote endpoint also requires an explicit variable
reference. Use `--keyless driver=true` for an explicitly unauthenticated remote
server; `false` disables automatic keyless detection and `auto` restores it.
URLs cannot contain user info, query strings, or fragments. Inline API keys and
authentication headers in `extra_params` are rejected.

Location is an operator assertion. A loopback proxy may still run inference
remotely, so choose `remote` for that case; connectivity does not verify local
execution or zero cost. LAN and public endpoints cannot be classified `local`.
Endpoint-configured Driver and Reviewer require distinct
concrete `canonical_model_family` declarations. Use the same family identifier
across providers, aliases, and quantizations of the same model. `auto`, profile,
smart, and fusion routes cannot prove diversity and are rejected. Plain existing
model configurations retain compatibility using their requested identity with
the provider prefix removed. Changing a role's model clears its old family
declaration; supply the new family when changing an endpoint-configured Driver
or Reviewer. Runtime resolution evidence remains BTN-54 work, and cost-policy
enforcement remains BTN-55 work.

## 5. Run Battalion and review the human checkpoint

Now start the example Run:

<!-- check:run -->
```powershell
& $Python -m battalion run BTN-HELLO-1 --spec ticket.md --checkpoint driver --budget 20
```

`BTN-HELLO-1` is only a local example identifier; this command does not create a
GitHub Issue. The `driver` checkpoint tells Battalion to pause after Architect
and before Driver RED. The budget counts execution turns, not dollars, and is
not a provider spending limit.

Battalion prints a canonical Run UUID. Copy it and inspect the saved Run:

<!-- check:status -->
```powershell
$RunId = Read-Host 'Paste the printed Run UUID'
[guid]::Parse($RunId) | Out-Null
& $Python -m battalion status $RunId --human
```

You should see:

- `Status: awaiting-human`
- a `manual-checkpoint` interrupt
- an Architect `plan.md`

Read the plan before continuing. Confirm that it matches the ticket and stays
within the intended paths and scope. If Battalion stopped earlier for another
reason, investigate that reason instead of treating it as the expected
checkpoint.

When you are satisfied with the plan, resume the same Run:

<!-- check:resume -->
```powershell
Get-Content -LiteralPath 'plan.md'
& $Python -m battalion resume $RunId --resolution 'Reviewed the greeting plan and approved continuation within src/'
& $Python -m battalion status $RunId --human --costs
& $Python -m pytest -q
```

A successful first Run should end with `Status: done`, passing tests, and the
same Run UUID. Its execution history should show Architect, Driver RED,
Reviewer RED, Driver GREEN, Reviewer GREEN, Refactorer, and the final Reviewer
check, along with your human resolution.

A Refactorer `no-change` result can be valid, but it still goes through review.
If Battalion pauses again, the Run is not complete: inspect the new interrupt
before deciding whether to resume.

A command exiting successfully is also not enough to prove completion. The
saved Run status is authoritative.

`status` and `resume` must be run from the same project. Running `battalion run`
again creates a **new** Run; it does not resume this one.

For the structured record:

<!-- check:evidence -->
```powershell
& $Python -m battalion status $RunId
```

Keep the Run UUID and relevant provenance/evidence for UAT. The canonical state
is stored under `.battalion/state/`. Unknown monetary cost remains unknown; it
is never silently treated as zero.

`--trace-output` is optional and is not needed for this first Run. It records
raw provider text and may contain sensitive information.

## 6. Optional: open the Windows desktop application

The desktop ZIP contains the Qt client and its worker runtime, but it does not
yet contain a first-run setup wizard. Complete the CLI setup above first.

The CLI wheel and desktop ZIP must have the same version **and source commit**.
The desktop worker is its own packaged runtime; installing something into the
CLI virtual environment does not automatically install it into the worker.

The current desktop build is not code-signed. Windows SmartScreen or your
organization's policy may therefore warn or block it. Do not disable those
protections. Verify the artifact and follow your organization's approval
process.

With `Assert-ArtifactHash` from step 2 still available:

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

Keep the extracted directory intact. Do not move only `Battalion.exe`; it needs
its sibling files and worker directory.

Launch the application from the same shell if your provider credentials are in
environment variables:

<!-- check:desktop-launch -->
```powershell
& $DesktopExe --project $Project
```

The project path determines which Battalion Runs the UI displays. **Work** shows
active or actionable Runs. **History** shows terminal Runs, including the CLI
example you just completed. `Ctrl+R` reloads authoritative state.

For a paused Run, inspect the Actor and resolution before choosing **Resolve and
resume**. Closing the UI does not guarantee that its detached worker has
stopped.

### Current desktop execution limitation

The current packaged worker excludes pytest, while Reviewer expects to launch
pytest through its own executable. As a result, the current ZIP can be used to
inspect Runs and verify packaged prompts, but that does **not** prove that a
full desktop Run can complete.

Installing pytest in the CLI environment does not repair this packaging
boundary. Record the packaged Reviewer failure in [desktop UAT](uat/desktop.md)
and use an identified corrected build for acceptance. Do not silently switch to
source mode and call the ZIP accepted. This is tracked by BTN-132.

## What next?

If this first Run works, you have verified the basic Battalion workflow:
installation, model setup, planning, a human checkpoint, RED/GREEN execution,
review, refactoring, persistence, and inspection.

For more detail:

- [Operator workflows](ui/workflow.md) explains the desktop workflow.
- [Troubleshooting and recovery](troubleshooting.md) covers failed or interrupted Runs.
- [Data handling and trust boundaries](data-handling.md) explains what Battalion stores and sends.
- [Contributor guidance](contributing.md) covers source development, which is a separate installation path.

The [CLI UAT](uat/cli.md) and [desktop UAT](uat/desktop.md) scripts use this guide
for formal onboarding validation. Automated checks are not a substitute for
human UAT acceptance.
