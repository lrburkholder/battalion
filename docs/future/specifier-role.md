# Specifier Role Planning Brief

**Status:** Draft
**Ticket:** BTN-45
**Target:** Post-v2 architecture
**Related:** `philosophy.md`, `spec.md`, BTN-25, BTN-26, RFC-0004

## Question

Should Battalion add a Specifier role, and if so, what durable input does it
produce before the Architect plans implementation?

The current graph starts with an already-supplied ticket specification. A
Specifier could help turn an operator's product intent into that artifact, but
adding it to the execution graph would change role authority, graph ordering,
interrupt behavior, context assembly, and the meaning of a run. Those changes
require an accepted RFC and, where architectural choices become durable, an
ADR before implementation.

## Candidate responsibility

A Specifier may propose a testable ticket specification containing outcomes,
constraints, non-goals, acceptance criteria, unresolved product decisions, and
links to governing repository artifacts. Its output should be inspectable and
editable by the human operator.

It must not silently make product or architecture decisions, approve its own
specification, edit implementation files, or bypass the Architect. Whether it
runs before a Battalion run, as a graph phase, or as a separate workflow is an
open decision rather than an assumption.

## Required decisions

- Entry conditions and the minimum operator input.
- The versioned specification artifact and its persistence owner.
- Human review, revision, acceptance, rejection, and cancellation semantics.
- Whether acceptance starts a new run or advances an existing run.
- Write scope, tool access, context budget, prompt contract, and model policy.
- Interaction with Architect planning, ticket identity, manual checkpoints,
  execution evidence, and cost reporting.
- Behavior for ambiguity, conflicting sources of truth, and missing product
  decisions.

## Evidence to gather

Use representative tickets to compare the current human-authored workflow with
one or more disposable Specifier workflows. Record specification completeness,
operator correction effort, unresolved-decision visibility, provenance, and
failure behavior. Do not use provider-private reasoning as acceptance evidence.

## Deliverable

BTN-45 produces a decision-ready RFC that either rejects the role, keeps it
outside the execution graph, or defines its exact authority and lifecycle. It
must decompose any accepted implementation into separate tickets and identify
required updates to `spec.md`, ADRs, prompts, state, graph transitions,
interrupts, scopes, application operations, documentation, and tests.

## Non-goals

- Implementing a Specifier node or prompt.
- Reintroducing Teacher or Researcher as Battalion roles.
- Allowing generated specifications to become authoritative without a visible
  human decision.
