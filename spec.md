# Battalion v1 — Spec

## Goal
Build Battalion: a LangGraph-based orchestrator that runs Architect → Driver →
Reviewer → Refactorer as a connected graph with explicit, human-controlled
interrupt points, replacing manual slash-command handoff with
handoff-to-orchestrator while preserving human oversight at defined decision
points. Battalion's first project is itself (dogfooding) — this spec is
produced using the Researcher/Specifier methodology it's meant to eventually
run automatically.

## In Scope (v1)
- LangGraph graph with four nodes: Architect, Driver, Reviewer, Refactorer —
  full rewrites, not wrappers over the existing Copilot prompts or Regi-
  agents. Refactorer was added during the architecture pass (see plan.md
  ADR-008) to complete the RED → Reviewer → GREEN → Reviewer → Refactorer →
  Reviewer loop; not part of the original spec draft.
- LiteLLM as the model access layer (per-node model configurability, not
  hardcoded to one provider)
- Local JSON as the default run-state persistence layer, with explicit
  versioned schema conventions
- A versioned state schema (JSON) shared across all four nodes
- Per-node declared, enforced write scope
- The v1 interrupt taxonomy (see below)
- CLI entry point, local machine execution

## Out of Scope (v1 — explicitly deferred)
- A Specifier role. Post-v2 architecture work may evaluate one, but no role or
  graph placement is accepted yet.
- Researcher and Teacher nodes. Teacher is no longer planned as a Battalion
  role, and the Researcher product boundary remains unresolved.
- Guardian and any severity-based review triggers. Future architecture work
  must first determine whether a distinct role is warranted.
- Live JIRA/MCP ticket integration. Local JSON is the v1 default; a plugin
  architecture is planned for specification, not built or accepted yet.
- Battalion self-modifying its own graph/node definitions at runtime. Future
  research is limited to carefully bounded, human-authorized proposals; the
  dogfooding model is "used to build Battalion's next work," not autonomous
  self-editing.
- Dynamic workflow admission or graph dispatch. BTN-138 provides a finite,
  versioned `WorkflowRecipe` policy vocabulary and BTN-139 provides an
  inspectable deterministic evidence assessment for post-v2 work. BTN-141 adds
  a pre-execution, Actor-authorized human decision contract, but none of these
  alters the v1 graph or allows model-produced node lists. Compact execution,
  admission persistence, and dispatch remain separately scoped follow-up work
  under RFC-0012.

## Accepted Post-v1 Inference Contract (delivery pending)

[RFC-0005](docs/rfcs/rfc0005.md) and
[ADR-0024](docs/adrs/adr0024.md) accept an endpoint-aware inference identity
and cost policy without changing the shipped v1 runtime. BTN-52 through BTN-55
deliver the contract; until those tickets complete, the existing LiteLLM
model-string configuration, BTN-14 string comparison, and BTN-35 call evidence
remain the implemented behavior.

The accepted identity separates the Battalion-requested model, resolved or
response model, provider, backend and non-secret endpoint, inference location,
canonical model family, and cost policy. Local Ollama, LM Studio, vLLM, and
other OpenAI-compatible services, plus remote inference through FreeLLMAPI, are
optional sources behind LiteLLM. No source or router owns Battalion's role,
graph, model-selection, diversity, retry, cost, or failure policy. A localhost
proxy proves only a local first hop, not local inference.

The accepted policies are `local-only`, `free-only`, and backwards-compatible
`paid-capable`. Local-only admits verified same-host inference. Free-only
admits verified local or current, sourced verified-free targets. Unknown
classification fails closed in both zero-cost modes, and retry or failover may
not cross the configured canonical-model, inference-location, or cost-policy
boundary. Paid-capable permits only effective configured targets; it does not
authorize arbitrary paid fallback.

BTN-14 diversity is defined over distinct canonical model families. Different
providers, endpoints, quantizations, or aliases alone do not qualify. Opaque
auto, profile, smart, or fusion routes cannot serve Driver or Reviewer unless
their family is constrained and mechanically proven before use. A runtime
identity contradiction invalidates the affected output or verdict and pauses
through interrupt condition 5.

BTN-35 monetary semantics remain unchanged: admission evidence and accounting
evidence are separate, missing monetary evidence is unknown rather than zero,
and a reported non-zero amount under a zero-cost policy is recorded before the
policy failure pauses execution. Credentials remain in the approved secret
boundary; endpoint URLs and bounded non-secret classification evidence may be
persisted.

## State Schema (v1, draft)
Uses explicit `schema_version` and enum-constrained `status` fields. Lives as one
versioned contract alongside the graph code; all four nodes validate against
it rather than maintaining separate sub-schemas.

Fields per ticket/run (draft — to be refined during Architect phase):
- `schema_version`
- `run_id` (UUIDv4 canonical identifier for new runs; pre-BTN-32
  human-readable identifiers remain valid for compatibility)
- `run_alias` (optional human-readable display label; never a persistence key)
- `project_id` (project-local UUID from `.battalion/project.json`; optional on
  legacy state)
- `ticket_id`
- `spec` (the supplied ticket specification, persisted for every role and resume)
- `status` (enum: not-started, in-progress, blocked, awaiting-human,
  done, failed-infra)
- `phase` (which node currently owns the ticket)
- `write_scope` (per-phase declared file scope, bound before writes; supports
  `driver_red`, `driver_green`, and `refactorer`, with legacy `driver` fallback)
- `reviewer_rejection_history` (list of {cause, cycle_number, checkpoint} —
  root-cause tracked for interrupt trigger #1. checkpoint (added in v1.1,
  BTN-12) scopes cycle_number to be per-checkpoint-type (red-check,
  green-check, refactor-check), not ticket-wide — see
  `docs/adrs/adr0009.md`.)
- `retry_bound` (configurable per ticket, per open decision)
- `budget` (tracked per graph run, not per node)
- `interrupt_log` (list of {trigger, timestamp, resolution})
- `interventions` (bounded typed human context with action ID, durable Actor
  ID plus immutable display snapshot, time,
  exact Architect/Driver RED/Driver GREEN/Refactorer target, queued/delivered
  disposition, and the sole receiving node-attempt ID)
- `human_action_log` (append-only run-action evidence with durable Actor ID
  plus immutable display snapshot, time, target, disposition, detail, and
  resulting durable state version/status/phase; legacy entries may contain
  only their literal pre-BTN-59 actor string)
- `manual_checkpoints` (list of phase names the user has declared a mandatory
  pause after — supports interrupt trigger #6)
- `execution_record` (a separately versioned, validated history of node
  attempts; see ADR-0014)
- `side_effect_ledger` (a separately versioned, validated ledger of externally
  visible side effects: stable Battalion logical operation IDs minted before
  first delivery, ordered attempt evidence with typed succeeded/failed/
  ambiguous outcomes, reconciliation records for unresolved operations, Run
  identity plus Actor identity where applicable, capability/integration/
  operation references, timestamps, bounded detail text, and optional SHA-256
  request digests; never secrets or external payload contents — see
  ADR-0029)

New-run construction belongs to the shared application boundary. It generates
the canonical run UUID and project marker before execution; graph nodes cannot
replace that identity. `.battalion/runs.json` is a project-scoped, rebuildable
catalog whose references use canonical run IDs. Moving a repository with its
`.battalion` directory preserves project identity. Legacy files are discovered
under their original IDs without rewriting historical provenance (ADR-0020).

### Actor identity and local provenance

Actor identity is a versioned, project-local contract persisted in
`.battalion/actors.json`. `actor_id` is an opaque UUID owned by Battalion and
never derived from a display name, operating-system username, email address,
provider login, or authentication subject. Each Actor records `kind` (`human`
or `system`), mutable `display_name`, `status`, timezone-aware creation time,
creating Actor reference, and bounded creation provenance. Authentication,
credentials, project authorization, assignment, and ownership are separate
contracts and are not Actor identity.

Project first use atomically creates the first local human Actor and a one-time
bootstrap event credited to that Actor. The event binds the Actor to the
project and records the accepted initial capability vocabulary; mechanical
capability enforcement remains BTN-60. The ceremony needs no network or hosted
account and does not inspect the operating-system username. Later local Actor
selection uses `actor_id`, so duplicate or changed display names cannot change
identity.

New interrupt resolutions, interventions, Recon decisions, and accepted-Intel
provenance record the durable `actor_id` together with an immutable display
snapshot. Renaming an Actor changes only the Actor projection, not historical
evidence. Pre-BTN-59 evidence containing only a string remains readable and is
rendered explicitly as legacy attribution; Battalion never matches it to a new
Actor by name or other mutable text. System activity may reference an Actor
whose kind is explicitly `system`, and human application operations reject
system or disabled Actors.

Actor establishment and inspection are shared application operations. CLI and
desktop source execution select the project's local human Actor and create the
default local trust root on first use without authentication infrastructure.

### Human actions and next-attempt context

Interrupt resolution is persisted before resume and records the human Actor
ID, immutable display snapshot, time, interrupt target, resolution,
disposition, and resulting durable state.
On the BTN-165 branch, resolution and a `resume_intent` linking that exact
action are saved atomically. Until a completed attempt has a durable outcome,
replaying resume reuses this intent instead of creating another human decision.
CLI and desktop clients submit the same `ResumeRun` application command; graph
resume inference and execution remain canonical.

V1 has exactly two intervention intents. `correction` targets `driver_red`,
`driver_green`, or `refactorer`; `design-decision` targets `architect`. Reviewer
is not a target, and neither Reviewer verdict override nor manual checkpoint
override is introduced. Submission is rejected while an active worker may have
a provider generation in flight.

Queued interventions are associated with a generated node-attempt identifier
and checkpointed before that target assembles context or calls a provider. The
attempt receives a named bounded Human intervention section, and its execution
record retains a hashed context reference to the action. Delivered content is
not supplied to later attempts or other roles. A crash before association
leaves the item queued; a crash after association preserves the receiving
attempt identity (ADR-0023).

BTN-165 registers the unfinished `NodeExecution` and its intervention delivery
in the same atomic state replacement. The graph then checkpoints
`attempt-started` before role execution. Recovery before that boundary reuses
the same attempt ID and requires the original prompt/model configuration;
after that boundary, absent a saved outcome, execution may have caused writes
or provider charges and must not be automatically replayed. Completed outcomes
are checkpointed before completion observations, with their exact graph
successor, and then marked `outcome-checkpointed` by the graph wrapper.
The durable `graph_progress` contract distinguishes
`interrupted-before-attempt`, `attempt-created`, `attempt-started`,
`attempt-completed`, and `outcome-checkpointed`. A saved resume intent without
a graph cursor also explicitly represents authorization before attempt creation.
Correction context and its consumed retry allowance survive recovery; an
intervention remains exclusive to its originally receiving attempt.

Resume/intervention clients may supply a stable `action_id`. Replays retain
original actor, timestamp, target, and decision evidence; conflicting ID reuse
is rejected. A completed resume action replay is a read of current durable
state, not authorization to resolve a later interrupt. Without an ID, only a
pending resume intent is implicitly reused. The CLI exposes `--action-id`.
These IDs identify requests; repeating identical intervention text with a new
ID is a new human action.

Recursion-limit handling retains the latest checkpoint and its exact next
node. Unexpected graph exceptions never save invocation input over newer
durable progress. Application results/inspection expose typed recovery
assessments; execution failures become `RunRecoverable` or `RunRecoveryUnsafe`.
CLI and desktop explain whether replay is safe. An unknown started-attempt
outcome is terminal for automatic recovery: inspect the workspace and execution
record, then start a new run from the reviewed workspace. No manual JSON edits,
automatic write rollback, or exactly-once provider calls are promised.

Recon candidate accept, edit-and-accept, and reject operations remain outside
`RunState`. Application commands delegate to the audited Intel workflow, which
leaves candidate Markdown immutable and creates separate accepted Intel and
append-only review-decision evidence.

### Durable execution record

`execution_record.schema_version` is `1.7` on the BTN-165 branch; persisted
`1.0` through `1.6` records remain readable. Each role-node attempt appends one
record containing a stable execution identifier, role and graph phase, model
identity, start/end timestamps, outcome, bounded input references, and an
output reference or Reviewer verdict. Reviewer records link the clean-tree
test outcome and acceptance decision. Tool activity, interrupts, and produced
artifact provenance carry or reference the originating node execution.
An unfinished attempt has outcome `in-progress` and no end timestamp. Its
completed evidence replaces that entry under the same execution ID; legacy
completed evidence is not rewritten.

Each successful LiteLLM completion also records a bounded call identifier,
provider-reported model, and input/output token counts on its originating node
execution. Monetary cost is a nullable decimal amount with separate ISO 4217
currency and source (`provider-reported`, `estimated`, or `unknown`). Known zero
and unavailable cost are distinct, and token usage remains inspectable when
cost is unknown. This evidence is queryable by phase, role, currency, and
source. It is separate from the integer run-level `budget`, whose unchanged
semantics drive interrupt trigger #3 (see ADR-0017).

Artifact provenance stores the project-relative path, SHA-256 digest,
originating run, and originating node execution. It does not copy artifact
contents into `RunState`. Input references are likewise bounded pointers to
persisted state or workspace artifacts. The record is part of `RunState`, so
normal JSON save/load and graph pause/resume preserve the same evidence without
a second persistence or resumption path.

Version `1.2` adds bounded operator handoffs; explicit role prompt contract,
template hash, model-configuration identity, and Battalion revision evidence;
Git base commit, object algorithm, branch/detached state, and start/end dirty
state; and SHA-256 context references with inclusion and truncation metadata.
It retains no prompt/template contents, source contents, configuration values,
or dirty-worktree patch. A dirty endpoint therefore carries an explicit
`dirty-workspace-patch-not-retained` limitation and cannot claim exact
reconstructability.

Version `1.3` adds bounded counts of streamed reasoning and content characters
for each node attempt. These counts support model/phase comparison without
persisting raw provider reasoning or creating a second trace store.

Version `1.4` distinguishes an accepted role outcome from a rejected model
candidate. A typed pre-write role-contract violation records its reason,
offending paths where safe, correction attempt number, no-mutation guarantee,
and retry or escalation disposition. Battalion supplies one deterministic
automatic correction retry to the same role and phase; it consumes the normal
Run budget. A repeated violation pauses through the established human-interrupt
path. This never weakens scoped-write or other authority-violation handling.

Version `1.5` adds an optional, versioned `role_result` to node execution
evidence. Driver RED/GREEN may record `completed-with-change`, `blocked`, or
`escalated`; Refactorer also records `completed-with-no-change` when its
existing explicit no-op contract applies. Battalion validates role/mode policy,
bounded reason codes, evidence references against the input evidence supplied
to that node attempt, and observed artifacts before it constructs this record.
A blocked result preserves the incomplete stage and
ends the current run until a human records that the missing condition has been
addressed; an escalated result enters the existing durable human-resolution
boundary. Neither route advances through the normal success edge, and malformed
or prohibited output remains a deterministic failure.

Version `1.6` adds optional `test_execution` evidence to Reviewer attempts:
the exact command, temporary working-directory identity, exit classification,
return code, collected-test/failure/error counts when available, duration,
configured timeout, cancellation/timeout disposition, and process-tree cleanup
result. Each stdout/stderr stream retains at most 64 KiB with observed-byte and
truncation metadata. Invalid harness outcomes have an `unavailable` review
verdict rather than a fabricated test failure or rejection cause. Legacy
records retain their old inferred outcomes and expose process evidence as
unavailable. New Reviewer snapshot hashes describe only materialized inputs.

### Instinct data contract

BTN-20 introduces a separately versioned `1.0` contract under
`battalion.intel.models`. An Instinct has a stable `INS-...` identifier,
recommendation, bounded execution-record evidence, role audience, explicit
applicability, tags, creation provenance, and an optional identifier for the
older Instinct it supersedes. Supersession creates a forward reference from the
new record; it does not replace or rewrite the earlier record.

Lifecycle is a discriminated contract rather than an unchecked string:

- `CandidateInstinct` has lifecycle `candidate` and no authority as knowledge.
- `AcceptedInstinct` has lifecycle `accepted` and requires separate human
  acceptance provenance.

Both forms reject undeclared fields. In particular, confidence is not creation
metadata. Operational confidence remains deferred until retrieval usage and
operator-feedback evidence exist. This contract does not add Recon to the
graph or implement retrieval.

### Recon candidate generation

Recon is a post-completion role, not part of the execution graph that produces
the completed run. It accepts only a terminal `RunState` containing the BTN-19
durable execution record plus accepted Instincts explicitly supplied for
duplicate comparison. Conversation history is not an input.

Recon returns zero or more `CandidateInstinct` values separately from
`RunState`. Every candidate validates against the BTN-20 contract and cites a
run ID, node execution ID, and canonical record reference that resolve to the
supplied completed execution. Candidates duplicating a supplied accepted
Instinct are excluded.

Recon has no write tools or Intel repository access. It cannot change the
completed execution, publish knowledge, modify standards or architecture, or
bypass the separate human review and promotion workflow tracked by BTN-23.

BTN-34 persists the returned values separately as create-only Markdown under
`<project>/.battalion/recon/candidates`. Each `INS-...md` document contains the
complete strict BTN-20 candidate contract in YAML front matter and a
deterministic human-readable rendering. Loading validates the contract, the
filename identifier, and the rendering; confidence remains forbidden.
Publication uses a same-directory temporary file and an atomic create-only
link, so a collision cannot replace evidence and an interrupted write cannot
expose a partial candidate.

### Operator review and Instinct promotion

BTN-23 provides the only candidate-to-knowledge transition. For each Recon
candidate, the operator explicitly accepts, edits then accepts, or rejects it.
Acceptance creates a separately validated `AcceptedInstinct` with human
acceptance provenance and persists it through the immutable Intel repository.
Editing may change only knowledge-content fields; it neither mutates the input
candidate nor replaces its Recon creation provenance. Rejection performs no
Intel repository write.

Each candidate receives at most one append-only decision record. The record
contains the operator action, decision timestamp, operator identity, candidate
identifier, and the resulting accepted identifier for either acceptance path.
Decision records remain outside `RunState`; the completed execution and Recon
retain no promotion authority.

Inbox discovery joins candidates to those separate decisions in identifier
order and projects `pending`, `promoted`, or `rejected` without editing the
candidate document. Rejected candidates are retained indefinitely as evidence;
rejection adds only the append-only decision and provides no deletion path.

### Immutable Intel repository

BTN-21 persists each `AcceptedInstinct` as local JSON under its stable
identifier. Repository writes use create-only semantics: an existing identifier
cannot be replaced, including with identical content. Candidate Instincts are
rejected at the repository boundary.

Changed guidance is stored under a new identifier whose `supersedes_id` must
refer to an existing accepted record. Both records remain directly retrievable
for provenance. Active listing excludes every record referenced as superseded,
without deleting or editing its persisted history. Semantic indexing, remote
storage and cross-project sharing remain deferred.

### Deterministic Instinct retrieval

BTN-24 retrieves only accepted, active records returned by the immutable Intel
repository. Audience membership is mandatory. Applicability exclusions take
precedence; a non-empty inclusion list requires at least one literal normalized
match against the ticket and specification. Eligible records are ordered by
descending applicability-match count, descending tag-match count, then stable
Instinct identifier. Normalization case-folds and treats punctuation as word
separators. It does not perform semantic inference.

Each execution role queries independently. Selected Instincts are rendered
through the BTN-26 context assembler with stable identifier, recommendation,
applicability, and tags. The dedicated Instinct allowance admits only whole
entries inside the existing overall context bound. Reviewer receives this
context when it invokes its rejection-cause model; accepted mechanical reviews
do not make an LLM call.

Retrieval decisions explain why each active record was included or excluded.
Semantic or embedding-based retrieval, operator feedback, confidence scoring,
and cross-project sharing remain out of scope.

Role context is assembled deterministically from selected Instincts, this
persisted specification, the approved plan, and files under the declared Driver
source roots. Context is bounded before each LLM call: RED receives existing
implementation context, GREEN receives accepted RED tests, and Refactorer
receives the passing file set.

For a graph execution with GREEN artifact provenance, Refactorer receives the
latest successful GREEN Driver's production artifact paths and may write only
those paths. Its scope remains a structural ceiling, but artifact provenance is
the narrower task boundary. Refactorer may not create or modify tests,
documentation, configuration, or examples; its prompt also forbids adding
comments or docstrings. An attempted path outside the recorded GREEN artifact
set is a typed role-output failure and follows the documented human interrupt
path. A no-op remains valid when no behavior-preserving code simplification is
warranted.

## Interrupt Taxonomy (v1, final)
| # | Trigger | Definition | Handling |
|---|---|---|---|
| 1 | Reviewer rejects same root cause twice | Rejection cites substantially the same root cause as the prior rejection on that ticket | Pause, escalate to human with both rejection reasons shown |
| 2 | Out-of-scope write attempt | Node tries to write outside its declared write scope | Hard block, mechanical check, no LLM judgment involved |
| 3 | Budget exceeded | Tracked per graph run (whole ticket), not per node | Pause, show spend/turns so far, ask to continue/adjust/stop |
| 4 | Role-definition edit | Any action modifying a Battalion role/node definition | Always interrupt, no exceptions in v1 |
| 5 | Infra failure | Node crash, malformed state, malformed or contract-violating role output, or LiteLLM call fails after retries | Separate handling path — not folded into triggers 1 or 3; surfaces as a distinct failure state, not a judgment escalation |
| 6 | Manual checkpoint | User declares a checkpoint on the ticket/run config (e.g. "pause after Architect") independent of any system-detected condition | Graph pauses unconditionally at the declared point, regardless of whether any other trigger fired |

ADR-0024 accepts a post-v1 extension of trigger #5 for runtime inference
identity or zero-cost policy contradictions. That extension is not shipped
until BTN-54 and BTN-55 implement and validate it.

Deliberately deferred: severity-based ("critical finding") triggers. BTN-47
must determine whether this belongs in existing Reviewer and interrupt policy
or warrants a distinct Guardian role; no graph addition is accepted yet.

Trigger #6 added after reviewing github.com/Sdraugel/albert's `stop_after`
config pattern: the first 5 triggers are all system-detected conditions: this
one exists purely because the user asked for a stop at a specific point,
which the original taxonomy had no way to express.

## Write Scope Model
Each writing phase declares which project-relative roots it may create or edit.
Driver RED uses `driver_red`, Driver GREEN uses `driver_green`, and Refactorer
uses `refactorer`. A phase receives tools bound only to that entry; Reviewer
receives none. An explicitly empty phase entry grants no write authority.

BTN-166 branch contract: every configured directory and single-file root is a
project-relative authority declaration, not an arbitrary filesystem path.
Before tools are exposed, normalize both separator styles and prove that each
root resolves strictly within the resolved project base using native path
semantics (including Windows case and drive behavior). Reject absolute roots
even when inside the project, parent components, drive-relative/alternate-drive
and device paths, Windows path aliases, and symlink/junction escapes. No v1
role/mode permits a root resolving to the project itself. Internal links may
resolve to a contained root; binding pins that resolved authority and checks it
again on use, including single-file tools.

Invalid declarations raise `WriteScopeMisconfigured`; application start/resume
and worker boundaries expose `InvalidWriteScope` before mutation or execution.
Validate the complete declaration, including inactive phases, and the saved
Run's scopes on resume. Do not replace invalid scopes with defaults, retry them
as model-output corrections, or consume budget for them. This is a configuration
failure, not a new interrupt condition. A later bound-path redirection is an
audited `ScopeViolationError` using existing interrupt #2. Role-specific test-file
rules, Architect's single `plan.md` output, and Refactorer artifact provenance
restrictions still apply after containment validation.

For backward compatibility, a missing phase entry falls back to `driver`, whose
default is `["src/"]`. One-root output is relative to that root. Multi-root
output must prefix each path with a declared root. Absolute paths, traversal,
undeclared-root selection, and attempts to reach another phase's roots are
blocked before any file in the output batch is written and trigger interrupt
#2's audit path.

Reviewer independently copies and runs tests from the configured project root,
so test discovery is not coupled to a `src/` directory. For example, Battalion
itself can use `driver_red: ["tests/"]`, `driver_green: ["battalion/"]`, and
`refactorer: ["battalion/"]` without granting repository-wide write access.
See ADR-0013.

### Reviewer test execution (BTN-164 branch contract)

Reviewer runs `python -m pytest -q` with built-in JUnit output from a disposable
copy of the configured project root. A passing exit (0) requires positive test
counts and no failures/errors; a test-failure exit (1) requires collected tests,
positive failure counts, and no harness errors. Only the latter can satisfy
RED_CHECK. GREEN_CHECK and REFACTOR_CHECK require the former. The opposite valid
result is a normal Reviewer rejection; no tests (5), collection/usage/internal
errors (2–4), setup/teardown errors, unsupported exit codes, missing/malformed
JUnit output, launch failure, timeout, and cancellation never authorize progress.
They take typed infrastructure interrupt #5, retain evidence, make no
rejection-cause LLM call, and resume at the same Reviewer checkpoint. JUnit
parsing is capped at 8 MiB; larger output is an inspectable malformed result.

`reviewer_test_timeout_seconds` in project configuration defaults to 300 and
must be greater than zero and at most 3600. The bound applies to all Reviewer
checkpoints on start/resume. Each pytest process has its own process group;
timeout or cooperative/keyboard cancellation terminates descendants using
Windows tree termination or POSIX process-group signals with forced cleanup.
Cleanup attempts/results are recorded separately from test validity. Forced
termination of Battalion itself is not a cooperative cancellation and cannot
promise a final execution record.

Git snapshots admit current tracked files and nonignored untracked files, then
apply explicit generated-content exclusions. Non-Git snapshots walk regular
files with the same exclusions and prune virtual environments. Build/distribution
outputs (`build`, `dist`, `target`), coverage outputs, dependency/environment
directories, caches, `.battalion`, and VCS metadata are not test inputs.
Deleted tracked files, directory links, and links escaping the project are not
materialized. Internal file links are copied as independent regular files.
Snapshot writes remain Battalion-owned temporary IO, not Reviewer project write
authority or an OS sandbox. BTN-123 may replace the workspace mechanism but
must preserve this evidence and checkpoint-validity contract.

## Retry / Loop Bounds
Configurable per ticket rather than a fixed global constant — set as part of
the ticket's state at creation, adjustable by the human at any interrupt.

## Open Items for Architect Phase
- Exact JSON schema field types/validation rules (this spec sketches fields,
  doesn't finalize types)
- How write scope is declared and mechanically checked (LangGraph tool-binding
  design, not just the policy)
- Infra failure retry policy (how many LiteLLM retries before it counts as
  failure state #5)
- CLI UX for resuming a paused/interrupted run

## Acceptance Criteria (v1 milestone)
- A ticket can flow Architect → Driver (RED) → Reviewer → Driver (GREEN) →
  Reviewer → Refactorer → Reviewer end-to-end without human intervention when
  no interrupt trigger fires
- Each of the 6 interrupt triggers can be independently demonstrated (a
  scenario exists that reliably fires each one)
- A paused run can be resumed by a human after review, from the CLI
- No node can write outside its declared scope, verified by attempting an
  out-of-scope write and confirming it's blocked
- State persists to local JSON matching the versioned schema; a second CLI
  invocation can resume from a prior run's state file
