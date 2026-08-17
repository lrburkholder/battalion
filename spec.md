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
- Local JSON as the default state/ticket persistence layer, following
  `regiment-backlog.json`'s existing schema conventions
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

## State Schema (v1, draft)
Follows `regiment-backlog.json` conventions: explicit `schema_version`,
enum-constrained `status`, per-item dependency/blocking fields. Lives as one
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
- `manual_checkpoints` (list of phase names the user has declared a mandatory
  pause after — supports interrupt trigger #6)
- `execution_record` (a separately versioned, validated history of node
  attempts; see ADR-0014)

New-run construction belongs to the shared application boundary. It generates
the canonical run UUID and project marker before execution; graph nodes cannot
replace that identity. `.battalion/runs.json` is a project-scoped, rebuildable
catalog whose references use canonical run IDs. Moving a repository with its
`.battalion` directory preserves project identity. Legacy files are discovered
under their original IDs without rewriting historical provenance (ADR-0020).

### Durable execution record

`execution_record.schema_version` is `1.2` (BTN-33); persisted `1.0` and `1.1`
records remain readable. Each role-node attempt appends one
record containing a stable execution identifier, role and graph phase, model
identity, start/end timestamps, outcome, bounded input references, and an
output reference or Reviewer verdict. Reviewer records link the clean-tree
test outcome and acceptance decision. Tool activity, interrupts, and produced
artifact provenance carry or reference the originating node execution.

Each successful LiteLLM completion also records a bounded call identifier,
provider-reported model, input/output token counts, and US-dollar cost on its
originating node execution. This evidence is queryable by phase and role. It is
separate from the integer run-level `budget`, whose unchanged semantics drive
interrupt trigger #3 (see ADR-0017).

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

## Interrupt Taxonomy (v1, final)
| # | Trigger | Definition | Handling |
|---|---|---|---|
| 1 | Reviewer rejects same root cause twice | Rejection cites substantially the same root cause as the prior rejection on that ticket | Pause, escalate to human with both rejection reasons shown |
| 2 | Out-of-scope write attempt | Node tries to write outside its declared write scope | Hard block, mechanical check, no LLM judgment involved |
| 3 | Budget exceeded | Tracked per graph run (whole ticket), not per node | Pause, show spend/turns so far, ask to continue/adjust/stop |
| 4 | Role-definition edit | Any action modifying a Battalion role/node definition | Always interrupt, no exceptions in v1 |
| 5 | Infra failure | Node crash, malformed state, or LiteLLM call fails after retries | Separate handling path — not folded into triggers 1 or 3; surfaces as a distinct failure state, not a judgment escalation |
| 6 | Manual checkpoint | User declares a checkpoint on the ticket/run config (e.g. "pause after Architect") independent of any system-detected condition | Graph pauses unconditionally at the declared point, regardless of whether any other trigger fired |

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
