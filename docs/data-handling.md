# Data Handling and Trust Boundaries

Battalion works with your source code, specifications, model providers, and
saved Run evidence. Some of that information stays on your machine. Some may be
sent to services you configure.

**Only use project information that you are allowed to send to those services.**

This guide explains what Battalion may send, what it stores locally, where
credentials belong, and what to consider before sharing logs or traces. It is
not a privacy policy for model providers and does not claim regulatory
compliance.

If you are installing Battalion for the first time, start with
[Getting Started](getting-started.md).

## The short version

Before using Battalion with sensitive work, know these five things:

1. **Model providers can receive project content.** The exact content depends on
   the role, but it can include specifications, plans, source code, tests,
   accepted Intel, human interventions, and test diagnostics.
2. **Battalion saves evidence locally.** Run state, human decisions, execution
   history, model/token/cost evidence, diagnostics, and generated artifacts can
   contain sensitive information.
3. **Credentials do not belong in project files.** Use environment variables or
   another approved secret-management mechanism.
4. **Trace output is especially sensitive.** `--trace-output` can save raw model
   stream content and has no automatic redaction or expiry.
5. **Local does not automatically mean private.** A local-looking model name or
   endpoint does not prove that requests stay on your machine or are not logged.

## Where your information can go

| Destination | What may go there |
| --- | --- |
| Your local machine | Configuration, specifications, model results, Run state, generated files, test results, Intel, and other execution evidence. Battalion's JSON and Markdown files are not encrypted storage. |
| Your configured model provider or runtime | Role prompts plus the project context needed for that role. Setup validation also sends a small test completion. Retries can send similar information more than once. |
| Configured integrations | Selected events or capability requests when that integration is actually enabled and invoked. Credentials are resolved at the transport boundary rather than being included in portable configuration. |
| Terminal or desktop UI | Run status and evidence. Terminal scrollback, redirected output, screenshots, and screen sharing can create additional copies. |
| A trace file you request | Raw streamed model observations written to the path supplied with `--trace-output`. This file can live outside the project. |

Battalion does not make a blanket claim that every dependency, provider, proxy,
operating system, or user-configured service is free of telemetry or logging.
Review the services and deployment you actually use.

<a id="model-context"></a>
## What Battalion can send to a model

Battalion gives each role the context it needs for its job. That means different
roles can see different parts of the project.

| Role | Typical model context |
| --- | --- |
| Architect | Ticket ID, specification, accepted Instincts, and a delivered Design decision when one applies. The normal Architect context does not enumerate repository files. |
| Driver RED | Specification, accepted Instincts, approved plan, eligible existing implementation files, delivered Corrections, and bounded correction diagnostics when Battalion catches a role-contract violation. |
| Driver GREEN | Specification, accepted Instincts, approved plan, eligible RED test files, delivered Corrections, and bounded correction diagnostics when applicable. |
| Reviewer | Battalion runs mechanical tests first. When model review is needed, Reviewer can receive bounded test output and, when applicable, specification and accepted Instinct context. Test output may itself contain paths, source excerpts, or private diagnostics. |
| Refactorer | Specification, accepted Instincts, approved plan, eligible test and implementation context, accepted GREEN artifact paths, and delivered Corrections. |
| Recon | When explicitly invoked, Recon can receive the completed execution record and accepted Instincts used for comparison. That record may include diagnostics, human actions, role results, and provenance. Recon is not automatically invoked by ordinary `run` or `resume`. |
| Tactician | When used for an uncertain admission decision, Tactician receives the bounded assessment evidence supplied for that decision. Its recommendation is advisory; it does not grant authority. |

Battalion limits how much context it assembles, but **size limits are not secret
filters**. Eligible files are not scanned for secrets before being included.
`.gitignore` is also not a confidentiality boundary for model context.

Keep secrets and other information that should never reach a model outside the
configured roots Battalion is allowed to read.

Reviewer tests are different from model context. They run against a temporary
project snapshot. Tests execute with the Battalion process's operating-system
permissions and may be able to use inherited credentials or network access.
The snapshot is **not an OS security sandbox**.

Human intervention text is stored as evidence. A queued intervention is sent to
the model only when it is delivered to its intended attempt, but human text may
also appear later in execution evidence used by features such as Recon.

## Local and remote models

A remote provider receives Battalion requests outside the local Battalion
process.

A model server running on your machine can keep inference local, but do not infer
that from its name alone. A provider string containing `localhost`, `ollama`, or
another local-looking value does not prove that requests stay local, remain
offline, cost nothing, or are never logged. Proxies and local runtimes can also
forward or retain requests.

Verify the actual endpoint and routing you configured.

Battalion currently records the configured model identity and available
per-call model/token/cost evidence, but it does not always establish the final
routed provider or effective endpoint. More complete inference identity and
routing provenance is tracked by [BTN-54](https://github.com/lrburkholder/battalion/issues/87).

Driver and Reviewer must use different configured model identifiers. That is an
important diversity check, but different strings alone do not prove that two
requests ultimately reached independent backends.

`setup --no-validate` only skips the setup connectivity request. It does not
make later Battalion Runs offline.

<a id="credentials"></a>
## Where credentials belong

Do **not** put API keys or other secrets in:

- tickets or specifications,
- `plan.md`, source code, or tests,
- role prompts or human intervention text,
- `battalion.config.yaml`,
- screenshots or logs,
- Git history, or
- model identifiers and ordinary configuration values.

For model providers, use the environment variable expected by the configured
runtime or your organization's approved secret-management process. Battalion
does not automatically load a `.env` file merely because one exists.

For example, in PowerShell 7:

```powershell
$env:OPENAI_API_KEY = Read-Host 'Provider API key for this session' -MaskInput
# Run Battalion from this session without printing the variable.
# After all intended child processes have exited:
Remove-Item Env:OPENAI_API_KEY
```

An environment variable is still plaintext process data. Child processes can
inherit it. Removing it from the parent shell does not revoke the credential or
remove copies already inherited elsewhere. Rotate or revoke an exposed key at
the provider.

### Integration credentials

Portable integration configuration stores **references to credentials**, not the
credential values themselves. For example:

```yaml
credential_references:
  authorization:
    reference: env://AUTOMATION_WEBHOOK_AUTHORIZATION
```

The actual value should be supplied privately to the process that performs the
request.

Battalion validates recognized credential fields and reference formats, but it
cannot prove that arbitrary free text contains no secrets. Review configuration
before committing or sharing it.

<a id="local-evidence"></a>
## What Battalion stores locally

By default, Battalion keeps its operational state under `.battalion/` in the
project. Generated project files, such as `plan.md`, source changes, and tests,
remain in the workspace itself.

| Location | What it contains |
| --- | --- |
| `.battalion/state/<RUN_UUID>.json` | The canonical saved Run: specification/work-item information, scopes, budget, status, interrupts, human decisions, recovery state, and execution history. |
| Execution records inside Run state | Role attempts/results, model identity, token/cost evidence, artifact references and hashes, tool activity, review/test results, summaries, and provenance. This is not normally a raw prompt/response transcript, but it can still contain sensitive content. |
| Reviewer test evidence | Test command, outcome, counts, duration, timeout/cancellation information, and bounded stdout/stderr. Diagnostics may contain private project information. |
| Side-effect evidence | IDs and status used to track integration operations, retries, provider references, failures, and reconciliation. It is evidence, not a complete archive of every external request and response. |
| `.battalion/project.json`, `runs.json`, `actors.json` | Project identity, Run catalog information, Actor identities, and related mappings. |
| `.battalion/workers/` | Worker/process supervision information and bounded errors. |
| `.battalion/recon/` and `.battalion/intel/` | Recon candidates, human review decisions, and accepted Instinct records. Rejected candidates are retained rather than silently erased. |
| A path supplied to `--trace-output` | Raw trace JSONL. This can be outside the project and is managed separately from normal Run state. |

The CLI normally stores state relative to the project from which it is run. The
desktop uses its selected project. If you use custom application paths or trace
paths, data may be elsewhere.

Reviewer creates temporary snapshots and attempts to remove them after testing.
Cleanup is best effort, not secure deletion. A crash or operating-system failure
can leave temporary files behind.

Filesystem permissions, disk encryption, backups, cloud synchronization, and
other local storage controls remain the operator's responsibility.

<a id="traces"></a>
## Trace output and sharing diagnostics

Normal Battalion Run state does not store a complete raw prompt/response or raw
reasoning transcript. It can still contain specifications, human decisions,
model-produced role results, review causes, summaries, and test/error output.

`--trace-output PATH` is different. It writes raw streamed observations received
from the provider to a file you choose.

Trace files:

- can contain sensitive model output or reasoning,
- can be written outside the project,
- are appended to if you reuse the same path,
- are not automatically redacted,
- are not automatically encrypted,
- are not automatically rotated or deleted, and
- are not authoritative acceptance evidence for a Run.

Not using `--trace-output` avoids that file, but it does not prevent model calls
or guarantee that nothing sensitive appears in terminal output or normal Run
evidence.

Before sharing diagnostics, make a separate copy and inspect it manually. Prefer
a small sanitized excerpt containing the artifact/version and relevant failure
over uploading an entire Run, trace, or `.battalion` directory.

Do not edit the authoritative Run JSON merely to make it safe to share. Keep the
original evidence private and sanitize a copy instead.

## Retention, backup, deletion, and uninstall

Battalion currently keeps saved Runs and related evidence until you manage those
files. It does **not** currently provide a general:

- automatic retention or expiry policy,
- per-Run purge command,
- backup/restore service, or
- secure-delete guarantee.

Recon candidates and their review records are also retained as evidence.

If you need a diagnostic backup before recovery work, follow the narrow
[recovery backup procedure](troubleshooting.md#state-backup). Do not restore old
Run state over current state casually: external effects or file writes may
already have happened.

Removing Battalion's virtual environment or desktop application does not remove
project-local `.battalion` state, external trace files, terminal logs, backups,
provider-side data, integration-side data, or credentials stored elsewhere.
There is currently no Battalion-wide data-uninstall command.

Deletion or retention at a third-party model provider or integration is governed
by that service. Removing a local file cannot retract a request already sent to
another system.

<a id="integrations"></a>
## Integrations and authority

Battalion integrations do not automatically gain authority merely because they
are configured. Configuration, credentials, provider capability, and model
output are all separate from **human authorization**.

For outbound events, Battalion records Run state before attempting delivery and
tracks delivery attempts in durable side-effect evidence. An ambiguous external
result is not silently treated as success.

Current integration support is still bounded. In the reviewed baseline, ordinary
CLI `run`/`resume` and detached desktop workers do not automatically construct a
full integration runtime just because `battalion.integrations.yaml` exists.
Declaring valid configuration therefore does not prove that an event was sent.

When an outbound event sink is actually invoked, the event envelope is designed
to be small: identifiers, event type, timestamp, and a bounded summary rather
than a complete Run dump. Other capability adapters can have different request
contracts, so do not assume every integration receives exactly the same fields.

Integration credentials are resolved at the transport boundary and should not
be copied into portable project configuration.

## Important current limitations

Keep these limitations in mind when evaluating a Battalion deployment:

- Battalion cannot guarantee what a third-party provider logs, retains, trains
  on, or forwards.
- A local-looking model identifier does not prove local execution.
- Context-size limits do not remove secrets.
- `.gitignore` does not define what model context is safe.
- Reviewer test snapshots are not security sandboxes.
- Saved Run evidence can contain sensitive content even without trace export.
- Trace files have no automatic redaction, encryption, rotation, or expiry.
- Battalion has no general data-retention, purge, backup/restore, or secure-delete
  service today.
- Installing or uninstalling the application does not automatically manage all
  copies of project evidence.
- Configuring an integration does not itself prove that the integration is
  active or authorized.

These are boundaries to understand, not reasons to bypass Battalion's evidence,
write-scope, Actor, interrupt, or human-approval controls.

## Source and implementation notes

This guide describes the current user-facing data-handling contract. The detailed
architecture and historical decisions remain in the repository's ADRs, RFCs,
canonical GitHub Issues, and implementation tests. In particular:

- [BTN-172](https://github.com/lrburkholder/battalion/issues/268) established the
  public data-handling disclosure.
- [BTN-54](https://github.com/lrburkholder/battalion/issues/87) tracks stronger
  requested/effective model and routing identity evidence.
- [ADR-0024](adrs/adr0024.md) covers the inference configuration architecture.

For operational recovery, use [Troubleshooting and Recovery](troubleshooting.md).
For installation and a first disposable Run, use [Getting Started](getting-started.md).