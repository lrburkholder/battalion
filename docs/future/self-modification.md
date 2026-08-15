# Bounded Self-Modification Planning Brief

**Status:** Draft
**Ticket:** BTN-48
**Target:** Long-term research
**Related:** `philosophy.md`, `spec.md`, ADR-0002, ADR-0005, RFC-0004

## Question

Can Battalion safely propose improvements to its own graph and role definitions
while keeping architecture, authorization, and acceptance visibly human-owned?

For this planning track, self-modification means proposing a normal,
reviewable repository change through Battalion's existing engineering process.
It does not mean runtime mutation, automatic deployment, self-approval, or
unbounded authority over the repository that executes the proposal.

## Safety invariants

- A human explicitly defines the objective and authorizes every architectural
  decision and merge or deployment boundary.
- Proposed role, prompt, graph, scope, and interrupt edits remain ordinary
  version-controlled artifacts with visible diffs and provenance.
- No run changes the executable graph, prompts, tools, permissions, or
  persistence contract governing that same run.
- The proposing execution cannot review or approve its own change as the sole
  authority; model diversity and human checkpoints remain explicit.
- Scoped tools, tests, budgets, interrupts, application boundaries, and
  repository protections cannot be disabled by the proposal workflow.
- Failure leaves the last accepted system runnable and auditable.

## Required decisions

- Eligible change categories and permanently forbidden operations.
- Separation between the running Battalion instance and the candidate worktree
  or environment it modifies.
- Human checkpoints for intent, architecture, scope, validation, acceptance,
  merge, rollout, and rollback.
- Provenance tying objective, plan, prompts, models, tools, diffs, tests,
  reviews, decisions, and resulting revision together.
- Independent review, adversarial validation, canary or staged rollout, and
  recovery requirements.
- Recursion limits, budget limits, cancellation, compromised-state detection,
  and a mechanical stop mechanism.
- Criteria for demonstrating useful leverage without reducing operator
  understanding or control.

## Evidence progression

The RFC must define staged experiments that begin with read-only analysis and
patch proposals in an isolated disposable worktree. Later stages may be
considered only after earlier evidence is accepted. Experiments must include
rejected proposals, malicious or mistaken changes, failed validation, stale
base revisions, rollback, and attempted edits to protected authority surfaces.

## Deliverable

BTN-48 produces a decision-ready research RFC with a threat model, invariants,
staged evidence plan, stop conditions, audit contract, and explicit go/no-go
decision points. It may conclude that Battalion should not pursue this
capability. Any experiment or implementation requires its own ticket and human
approval after the RFC is accepted.

## Non-goals

- Implementing self-modification or automatically opening, merging, or
  deploying changes.
- Allowing a model to redefine its own authority, prompts, review criteria, or
  interrupt behavior during execution.
- Treating dogfooding as permission for autonomous self-editing.
