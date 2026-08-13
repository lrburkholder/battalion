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
- Researcher, Specifier, Teacher nodes (planned next, not this milestone)
- Guardian and any severity-based review triggers
- Live JIRA/MCP ticket integration (local JSON is the v1 default; plugin
  architecture is a named future extension point, not built now)
- Battalion self-modifying its own graph/node definitions at runtime — the
  dogfooding model is "used to build Battalion's next work," not autonomous
  self-editing

## State Schema (v1, draft)
Follows `regiment-backlog.json` conventions: explicit `schema_version`,
enum-constrained `status`, per-item dependency/blocking fields. Lives as one
versioned contract alongside the graph code; all four nodes validate against
it rather than maintaining separate sub-schemas.

Fields per ticket/run (draft — to be refined during Architect phase):
- `schema_version`
- `run_id`
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

### Durable execution record

`execution_record.schema_version` is `1.0`. Each role-node attempt appends one
record containing a stable execution identifier, role and graph phase, model
identity, start/end timestamps, outcome, bounded input references, and an
output reference or Reviewer verdict. Reviewer records link the clean-tree
test outcome and acceptance decision. Tool activity, interrupts, and produced
artifact provenance carry or reference the originating node execution.

Artifact provenance stores the project-relative path, SHA-256 digest,
originating run, and originating node execution. It does not copy artifact
contents into `RunState`. Input references are likewise bounded pointers to
persisted state or workspace artifacts. The record is part of `RunState`, so
normal JSON save/load and graph pause/resume preserve the same evidence without
a second persistence or resumption path.

Role context is assembled deterministically from this persisted specification,
the approved plan, and files under the declared Driver source roots. Context is
bounded before each LLM call: RED receives existing implementation context,
GREEN receives accepted RED tests, and Refactorer receives the passing file set.

## Interrupt Taxonomy (v1, final)
| # | Trigger | Definition | Handling |
|---|---|---|---|
| 1 | Reviewer rejects same root cause twice | Rejection cites substantially the same root cause as the prior rejection on that ticket | Pause, escalate to human with both rejection reasons shown |
| 2 | Out-of-scope write attempt | Node tries to write outside its declared write scope | Hard block, mechanical check, no LLM judgment involved |
| 3 | Budget exceeded | Tracked per graph run (whole ticket), not per node | Pause, show spend/turns so far, ask to continue/adjust/stop |
| 4 | Role-definition edit | Any action modifying a Battalion role/node definition | Always interrupt, no exceptions in v1 |
| 5 | Infra failure | Node crash, malformed state, or LiteLLM call fails after retries | Separate handling path — not folded into triggers 1 or 3; surfaces as a distinct failure state, not a judgment escalation |
| 6 | Manual checkpoint | User declares a checkpoint on the ticket/run config (e.g. "pause after Architect") independent of any system-detected condition | Graph pauses unconditionally at the declared point, regardless of whether any other trigger fired |

Deliberately deferred: severity-based ("critical finding") triggers. Reused
once Guardian joins the graph.

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
