# Data handling and trust boundaries

Read this before configuring a model, supplying project content, enabling an
integration, or exporting a trace. Battalion processes project information
locally and can send some of it to your configured services. **Use only data
you are authorized to disclose to those destinations.** Local evidence and
model output can be sensitive even when you never enable trace export.

This guide describes the implementation reviewed for BTN-172, including the
candidate work inherited from BTN-163–166. It is not a privacy policy for model
providers or a claim of regulatory compliance. Match it to your installed
artifact's source revision. Pages becomes public only after the main merge and
successful deployment; candidate reviewers should use this repository document
until then. The [source-to-claim review](#source-review) records the boundaries
and known gaps. Start installation with [Getting Started](getting-started.md).

## Where information goes

| Destination | Information and when it moves |
| --- | --- |
| Local Battalion process and filesystem | Reads configuration, specifications, admitted context, accepted Intel, and saved Runs. Processes model responses, runs project tests, writes scoped artifacts, and saves the evidence listed below. Plain JSON/Markdown persistence is not an encrypted vault. |
| Configured LLM provider/runtime | Setup validation sends a small `ping` completion with model selection and authentication where required. Execution sends the role prompt and the context described below, with configured generation parameters. Retries can repeat requests. A proxy can introduce further destinations. |
| Configured outbound integration | An enabled, constructed sink can receive selected domain events after Run state is durable. Other capability adapters have their own request contracts; the minimized event envelope does not describe all integrations. Credentials are resolved at the transport boundary. |
| CLI/desktop presentation | Status JSON can expose the full Run; human status, Work, History, and Intel show projections of local evidence. CLI live output can show raw streamed content/reasoning even without a trace file. Desktop live observations are transient; refreshed durable evidence is authoritative. Screenshots, terminal scrollback, screen sharing, and redirected output create additional copies under your control. |
| Opt-in trace export | CLI `run` or `resume --trace-output PATH` appends raw stream observations to a file you choose. This is separate from Run persistence, can be outside the project, and has no automatic redaction or expiry. |

There is no universal telemetry-absence claim here. Battalion's bounded evidence
and suppression of particular LiteLLM debug/stream-logging paths do not audit
every dependency, provider, proxy, callback, OS diagnostic, or operator-configured
service. Review the actual deployment and its settings before sensitive use.

<a id="model-context"></a>
## What can enter model context

The ordinary CLI/desktop execution path uses the full Implementation Run:
Architect, Driver RED, Reviewer, Driver GREEN, Reviewer, Refactorer, Reviewer.
Resume continues that path from durable evidence and may read updated on-disk
context. A role's packaged prompt (or explicitly selected override) is sent as
a system message. Configuration/model identifiers are not evidence that a
provider received only public content.

| Role or supported operation | Categories sent to its configured model |
| --- | --- |
| Architect | Ticket ID, specification, selected active accepted Instincts, and any delivered Design decision for that exact attempt, including its text, action ID, and Actor attribution. The standard assembler does not enumerate repository files for Architect. |
| Driver RED | Ticket/specification, selected accepted Instincts, delivered Correction, `plan.md` as approved-plan context, and eligible existing implementation files from the configured production roots. A bounded automatic role-contract correction can add Battalion's diagnostic and offending-path context. |
| Driver GREEN | Ticket/specification, selected accepted Instincts, delivered Correction, approved-plan context, and eligible RED test files from test roots; automatic correction context when applicable. It does not use RED's implementation-file selection. |
| Checkpoint Reviewer | Mechanical tests run first. Only a valid but rejected test outcome triggers the LLM call, with Reviewer prompt and bounded test output. When selected accepted Instincts are present, the graph also supplies ticket/specification and those Instincts. With no selected Instincts, that extra context is absent. Accepted tests and invalid harness outcomes do not call the model. No queued human intervention targets Reviewer. Test output may contain source excerpts, paths, or private diagnostic text. |
| Refactorer | Ticket/specification, selected accepted Instincts, delivered Correction, approved-plan context, eligible test/implementation files, and the authorized production artifact paths from the accepted GREEN attempt. Read context is broader than the files it may change. |
| Recon, when explicitly invoked by a caller | Recon prompt, Run ID, the completed `execution_record` serialized in full, and supplied accepted Instinct records for duplicate comparison. The execution record can contain test diagnostics, human-intervention evidence, role-result text, and provenance. It does not automatically serialize the whole Run/specification, although excerpts may already be in that evidence. Recon returns untrusted candidates; ordinary run/resume does not automatically invoke it. |
| Tactician advisory assessment, when invoked for uncertain admission | Tactician prompt and the supplied structured assessment input: deterministic admission assessment, bounded revision-pinned work-item/specification/context evidence text, known scope, registered recipe summaries, policy references, and human constraints. Its recommendation is advisory, not human authorization. |

The finite recipe registry, deterministic admission, human decisions, and compact
execution policy exist as application contracts. Compact selection/persistence
and graph dispatch are not integrated into ordinary run/resume in this reviewed
baseline (BTN-143). Independent semantic Review Runs are accepted architecture
with implementation still pending; the checkpoint Reviewer above is not that
future role. These contracts do not justify claiming additional automatic model
calls, durable admission files, or a UI workflow selector.

Context assembly is bounded: ordinary role context has a 32,000-character
allowance, eligible files are truncated to 8,000 characters each, and accepted
Instinct rendering has a separate 6,000-character cap within that context.
Truncation can omit later sections. **These are size limits, not secret filters.**
The repository-context reader selects supported text suffixes inside configured
phase roots; it does not apply `.gitignore` or scan for secrets. Keep private
files out of those roots. Reviewer test snapshots use a different admission
policy, including tracked/nonignored project files and generated-file exclusions.
Neither policy makes executing project tests an OS security sandbox: tests run
with the process's privileges and may access inherited credentials or network.

Queued interventions become context only for their delivered target attempt.
Ordinary interrupt-resolution text is retained as a human decision; it is not
automatically inserted as a new prompt instruction. Human action text or excerpts
can still be retained in execution evidence and reach Recon if invoked. Review
specifications, plans, code/tests, prompt overrides, accepted Intel, and human
text for disclosure suitability before calling a model.

## Local and remote inference

A remote provider receives requests outside the local Battalion process. A
compatible endpoint running on your machine can keep inference on that machine,
but a local-looking provider/model name is not proof of local execution, offline
operation, privacy, zero cost, or zero retention. Local servers and proxies may
log, forward, or back up requests. Verify the actual endpoint, routing, access
controls, and service policies yourself; this guide makes no provider-specific
training or retention promise.

Current evidence records configured model identity and per-call model/token/cost
information. It does **not** reliably classify effective endpoints or routed
providers. Requested/effective identity and alias-aware diversity provenance are
tracked by [BTN-54](https://github.com/lrburkholder/battalion/issues/87), under
[ADR-0024](adrs/adr0024.md). The operator deferred BTN-54 to post-UAT on
2026-08-30; it remains open and does not block delivery of this disclosure.
Do not infer locality from a display string or unknown
cost. Keep Driver and Reviewer configured with different models and verify their
actual routing; string diversity alone cannot establish independent backends.
`setup --no-validate` skips the connectivity completion, not subsequent model IO.

<a id="credentials"></a>
## Where credentials belong

Use your approved secret manager or environment-injection procedure. For model
setup, supply the provider environment variable expected by the installed
runtime (for example `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`). Merely writing a
`.env` file does not load it. The provider credential path is separate from
`BATTALION_MODEL_DRIVER` and similar model-selection variables.

For a PowerShell 7 session, this example avoids putting the secret value in a
saved command or printing it. It still creates a plaintext process environment
value, which child processes may inherit; it is not a vault:

```powershell
$env:OPENAI_API_KEY = Read-Host 'Provider API key for this session' -MaskInput
# Run the reviewed setup/Run commands in this session; never echo the variable.
# After all intended child processes have exited:
Remove-Item Env:OPENAI_API_KEY
```

This removes the parent session variable only; it neither revokes the credential
nor removes copies already inherited by workers. Use provider revocation/rotation
procedures for an exposed credential. Do not paste credentials into tickets,
specifications, plans, source/tests, prompts, human actions, screenshots, logs,
tracked YAML, or commit history. Fresh setup writes model identifiers, but it
preserves unrelated existing configuration keys; it is not a cleanup or
redaction operation. `extra_params` are passed to the runtime, so never store
literal keys there in tracked configuration.

Portable `battalion.integrations.yaml` stores **symbolic references**, not values:

```yaml
project:
  integrations:
    automation-events:
      integration_id: automation-events-primary
      provider: http-webhook
      transport: webhook
      capabilities: [outbound-event-sink]
      settings:
        endpoint: https://automation.example/events
        event_types: [human_interrupt, run_failed]
        timeout_seconds: 10
      credential_references:
        authorization:
          reference: env://AUTOMATION_WEBHOOK_AUTHORIZATION
```

Supply the referenced authorization value privately to the process that sends
the request. `keyring://battalion/automation` is also a valid symbolic form, but
the bundled webhook/Discord resolver supports only `env://`; keyring lookup
requires an explicitly injected environment-specific resolver. Battalion does
not provide a general keyring provisioning UI or use integration keyring
references for model setup. Discord stores its numeric webhook ID as a setting
and its secret token via `credential_references.webhook_token`; never paste a
complete token-bearing webhook URL into portable settings.

Integration validation rejects recognized secret-bearing field names and
invalid references. It cannot prove arbitrary text is secret-free. Endpoint
names, identifiers, aliases, Actor names, and free text must also be chosen
carefully. A symbolic reference is not itself a credential, but may reveal
internal account/service naming. Review configuration before sharing it.

<a id="local-evidence"></a>
## What is stored locally

These are defaults when operating from the project root. The CLI's state path
is relative to its working directory; `--base-dir` selects file-operation roots
and does not automatically relocate CLI state. Application callers can supply
other state directories. Desktop resolves state under its selected project.
Check the printed state path, selected project, and explicit trace path.

| Location | Contents |
| --- | --- |
| `.battalion/state/<RUN_UUID>.json` (legacy IDs remain readable) | Versioned Run, supplied specification and optional normalized work item; scopes, budget, phase/status, rejection history, interrupt context and resolutions; queued/delivered interventions, Actor attribution and human-action history; recovery intent and execution checkpoints. |
| `execution_record` inside that Run JSON | Node/attempt IDs, times, model identity, input/output references, normalized role results, rejected-candidate/no-write evidence, tool activity, test/review results, summaries, interruption links, and context provenance. Per-call input/output tokens, nullable cost/currency/source, and streamed character counts are evidence, not raw reasoning. Prompt provenance stores identity/path/version/hash rather than the rendered prompt. Artifact provenance stores paths/digests, not file snapshots; Git provenance does not retain dirty patches. |
| Reviewer `test_execution` inside that Run JSON | Command, scratch working-directory identity, outcome classification, collected-test counts, duration, timeout/cancellation disposition, and at most 64 KiB each of stdout/stderr plus truncation metadata. Treat diagnostic text as sensitive. |
| `side_effect_ledger` inside that Run JSON | Logical operation/deduplication IDs, Run/Actor/integration/provider/transport identity, attempts, timestamps, request digests, provider references, status/failure and reconciliation details. It is not a separate ledger directory or a full request/response archive. Detail text may still be sensitive. |
| `.battalion/project.json`, `runs.json`, `actors.json` | Project UUID/identity, Run catalog and state references; human/system Actor identities, bootstrap/selection evidence, and external-identity mappings where configured. These are personal/operational data, not authentication credentials. |
| `.battalion/workers/<RUN_UUID>.json` | Worker ID/PID, operation, lifecycle/timestamps, state path, cancellation and bounded errors. Locks also exist during coordination. Detached execution redirects stdout/stderr to the null device; this is not a promise that other diagnostics never exist. |
| `.battalion/recon/candidates/INS-*.md` | Immutable candidate Instincts with validated YAML front matter, recommendation/applicability and evidence provenance. |
| `.battalion/recon/decisions/INS-*.json`, `.battalion/intel/INS-*.json` | Separate immutable human review decisions and accepted Instinct records. Rejected candidates remain; active accepted records can later be selected into model context. |
| `plan.md`, admitted source/test paths, explicit trace path | Actual generated artifacts stay in the workspace. Trace JSONL stays wherever the operator placed it. Neither is contained by an artifact digest in Run state. |

Reviewer uses temporary project snapshots and attempts to remove them after
execution. Cleanup is best effort, not secure erasure; process/OS failure can
leave scratch files. Filesystem permissions, disk encryption, backup policy,
and exclusion from Git/cloud sync are operator responsibilities. `.gitignore`
alone provides neither access control nor a model-context confidentiality rule.

<a id="traces"></a>
## Raw output, traces, and sharing

The ordinary execution record does not store a raw prompt/response transcript
or raw streamed reasoning. That does **not** make it content-free: specification,
human text, model-produced review causes/role results, summaries, and test/error
diagnostics can be retained. Generated model content also becomes workspace
artifacts. Reading a Run as JSON exposes more than a concise human status view.

`--trace-output` appends received stream events with schema version, Run reference,
sequence, time, node, kind (`token` or `reasoning`), and raw content. It is not a
request-prompt export, a complete provider transcript, or acceptance evidence.
Provider streaming support determines what appears; a missing reasoning stream
does not prove the provider produced none. Reusing a path appends more sessions;
sequence numbers are session-local. There is no automatic rotation, redaction,
encryption, or deletion. The CLI warns before opening it. Omitting the flag
avoids this file export but does not disable model calls or all terminal output.
Desktop has no equivalent trace-export/setup wizard; setup uses the CLI, and
**Help -> Data handling (opens browser)** exposes this guide without running a model.

Before sharing, make a separate copy and inspect it manually. Remove private
specification/source excerpts, human/Actor identifiers, paths, endpoints, tokens,
and other sensitive text; do not assume a built-in redaction pass made it safe.
Prefer a minimal sanitized finding with artifact/version and error category to
uploading an entire Run, trace, or `.battalion` directory. Preserve original
evidence privately; never edit authoritative Run JSON to sanitize a report.

## Retention, backup, deletion, and uninstall

Current persistence retains saved Runs/evidence until an operator manages the
files; it has no general expiry, retention scheduler, per-Run purge command,
backup/restore service, or secure-delete guarantee. Recon candidates, decisions,
and accepted Intel use create-only persistence; rejection does not erase them.
Status JSON and opt-in trace JSONL are available outputs, not a portable full
project backup or a privacy-safe diagnostic bundle.

For a private diagnostic copy, follow the narrow, inactive-writers-only
[recovery backup procedure](troubleshooting.md#state-backup). Preserve relevant
workspace changes separately. Copying a Run alone misses artifacts, Intel and
external copies; restoring old state can replay already-applied effects. Do
not delete locks, reset ledgers, rewrite JSON, or recursively remove a project
as a recovery shortcut. A reviewed lifecycle/deletion feature requires a
separate contract and ticket; this guide does not invent one.

The wheel/desktop ZIP distribution has no Battalion data-uninstall routine.
Removing an environment or extracted application directory does not establish
that project state, external trace paths, credentials, backups, terminal logs,
or provider/integration copies were removed. Inventory exact locations and use
your approved retention/revocation process after all writers stop. Third-party
retention, exports, deletion and backups are governed by those services; local
file removal cannot retract a request already sent to them.

<a id="integrations"></a>
## Outbound integrations and authority

In this baseline, application callers must supply a constructed integration
runtime to deliver events. Ordinary CLI run/resume and detached desktop workers
do not construct that runtime from YAML alone. Declaring a binding validates
configuration; it is not evidence that any event was delivered. The contracts
below describe the adapters when actually invoked.

The current OutboundEventSink schema `1.0` supports `human_interrupt`,
`run_failed`, and `run_completed`. Its envelope has event ID/type/version/time,
bounded Run/project provenance (including optional alias and work-item ID),
and typed status/phase or interrupt ID/trigger data. It has no fields for raw
exception context, prompts, transcripts, source, model context, arbitrary Run
state, or credential values. This structural minimization cannot remove a
secret that an operator put in an identifier or alias.

The generic HTTP webhook adapter sends selected events to its configured
endpoint with transport authorization if configured and an `Idempotency-Key`.
It does not follow redirects. Discord's sink accepts only `human_interrupt`
and renders bounded Run/work-item/phase/reason information plus a copyable
status command; its webhook token is used below the adapter boundary. Delivery
may leave copies at the receiver. Ambiguous delivery requires reconciliation
before retry; a saved ledger does not guarantee deletion at the recipient.

See the [accepted integration boundary](rfcs/rfc0006.md#outboundeventsink) and
the [detailed current event schema][events]. Other implemented contracts such
as GitHub work-item reading/authorized mutation and Actor-targeted notification
routing have different inputs (for example issue content, mutation data, or
notification text/destination resolution). They are not automatically enabled
by selecting a model, nor does a configured capability name prove that an
adapter exists. Review each adapter and any future integration separately.

Provider capability, a valid credential, a model recommendation, and successful
integration delivery **do not grant human authorization**. The existing scoped
write tools, RED/GREEN/refactor reviews, interrupt decisions, and human Intel
promotion remain required. Outbound-event replies do not become a command channel.
Actor identity is attribution, not proof that all planned capability/authentication
enforcement exists. See [operator actions](ui/workflow.md),
[ADR-0023](adrs/adr0023.md), and [ADR-0026](adrs/adr0026.md).

<a id="source-review"></a>
## Source-to-claim review and delivery evidence

Reviewed against baseline `d5e3580d77a75de9e7dfb380ebcf0604cb0081d8` and BTN-172's
presentation-only notice/navigation changes. Links below pin runtime sources to
that baseline. The guide does not change context, authority, or retention policy.

| Disclosure | Source and verification boundary |
| --- | --- |
| Role inputs, file selection, bounds, Instincts and interventions | [Context assembly][context], [graph wiring][graph], [role implementations][nodes]; credential-free graph/node/context tests. Reviewer context is conditional on selected Instincts, not always the full specification. |
| Recon and Tactician; compact/Review Run limitations | [Recon caller][recon], [Tactician input/call][tactician], [application operations][application], [workflow execution policy][workflows]; `test_recon_node.py`, `test_tactician.py`, workflow tests; accepted [RFC-0012](rfcs/rfc0012.md) and [RFC-0014](rfcs/rfc0014.md) are not proof of integrated future execution. |
| Setup ping, model environment credentials, preserved config | [Setup][setup], [configuration][config]; `test_setup.py`. Setup and trace warning ordering are checked without providers in BTN-172 tests. |
| Requests/retries and limited inference identity | [LiteLLM wrapper][llm], [execution evidence][execution], [state models][state]; `test_litellm_client.py`; missing effective endpoint provenance is canonical BTN-54, not a local/privacy guarantee. |
| Integration references and actual keyring support | [Integration configuration][integration-config], [webhook resolver/transport][webhook], [Discord][discord]; integration configuration/webhook/Discord tests. README wording now distinguishes schema support from resolver implementation. |
| Events versus other capability payloads | [Event schema][events], [GitHub adapter][github], [notification routing][notifications], [ADR-0025](adrs/adr0025.md); outbound/integration/notification tests. Free-text fields and third-party destinations remain operator review boundaries. |
| Run, human decisions, prompt/code/artifact and effect evidence | [State models][state], [execution capture][execution], [state persistence][persistence], [side effects][effects]; persistence/execution/effect tests. Troubleshooting was corrected: the ledger is inside Run JSON, not a separate directory. |
| Identity, worker and Intel locations | [Identity][identity], [Actors][actors], [workers][workers], [Intel repositories][intel], [application][application]; identity/worker/Intel tests. |
| Tests, scratch files and diagnostic limits | [Reviewer process boundary][review-tests], [Reviewer node][reviewer], [ADR-0007](adrs/adr0007.md); Reviewer process/node tests. Process privileges are not a sandbox guarantee. |
| Trace fields, append behavior and presentation | [Progress/trace writer][progress], [CLI][cli], [observation contract][observation]; CLI/progress/desktop tests. No complete transcript, redaction, or retention inference follows from bounded durable evidence. |
| Lifecycle/uninstall limitations | [Persistence][persistence], [Intel repositories][intel], [CLI commands][cli], and [distribution contract](release.md). No implemented general purge/restore/data-uninstall API; manual backup guidance is diagnostic preservation only. |
| Authority and provider-neutral limits | [Specification](../spec.md), [ADR-0002](adrs/adr0002.md), [ADR-0023](adrs/adr0023.md), [ADR-0025](adrs/adr0025.md). Provider routing, retention, training, dependency telemetry and secure deletion are outside the guarantees established by these sources. |

Credential-free Pages checks stage this document, its navigation and rewritten
links. The [CLI UAT](uat/cli.md#data-handling) and
[desktop UAT](uat/desktop.md#data-handling) additions check pre-use disclosure,
evidence sensitivity, and trace handling using disposable content. The operator
approved both BTN-172 scenarios on 2026-08-30; retain the approved script revision
with formal execution evidence. This is script approval, not live acceptance.
Final live confirmation
belongs to BTN-129/BTN-132 after BTN-173; successful main Pages deployment is
verified by BTN-173, not asserted by local staging. Broader site framing remains
BTN-116. Any newly discovered code/contract conflict must become a canonical
code/policy ticket rather than an invented assurance in this guide.

[context]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/context.py
[graph]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/graph.py
[nodes]: https://github.com/lrburkholder/battalion/tree/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/nodes
[recon]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/nodes/recon.py
[tactician]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/tactician.py
[application]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/application.py
[workflows]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/workflow_execution.py
[setup]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/setup.py
[config]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/config.py
[llm]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/llm/litellm_client.py
[execution]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/execution.py
[state]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/state/models.py
[integration-config]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/integrations/configuration.py
[webhook]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/integrations/webhook.py
[discord]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/integrations/discord.py
[events]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/integrations/events.py
[github]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/integrations/github.py
[notifications]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/notifications.py
[persistence]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/state/persistence.py
[effects]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/integrations/effects.py
[identity]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/identity.py
[actors]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/actors.py
[workers]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/workers.py
[intel]: https://github.com/lrburkholder/battalion/tree/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/intel
[review-tests]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/reviewer_testing.py
[reviewer]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/nodes/reviewer.py
[progress]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/progress.py
[cli]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/cli.py
[observation]: https://github.com/lrburkholder/battalion/blob/d5e3580d77a75de9e7dfb380ebcf0604cb0081d8/battalion/observation.py
