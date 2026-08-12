# Backlog Proposal — Recon and Engineering Knowledge

**Status:** Accepted into `backlog.json` on 2026-08-11

This proposal replaces the earlier Recon-only ordering. Recon cannot produce
useful, reviewable knowledge until Battalion has a durable execution record and
an Instinct contract. Retrieval also depends on the bounded context assembly
tracked by BTN-26.

The canonical ticket definitions and acceptance criteria live in
`backlog.json`. The accepted sequence is:

## BTN-19 — Durable execution record and artifact provenance

Persist the evidence Recon will inspect: node inputs and outputs, review and
test results, tool activity, model identity, and produced artifacts.

## BTN-20 — Instinct data contract

Define candidate and accepted Instincts, including recommendation, evidence,
audience, applicability, tags, provenance, lifecycle status, and supersession
metadata. Confidence is not assigned at creation.

## BTN-21 — Immutable Intel repository

Persist accepted Instincts under stable identifiers. Accepted records are
immutable; changed guidance creates a new record that may supersede an older
one while preserving history.

## BTN-22 — Recon candidate generation

After a completed run, inspect its durable execution record and produce zero or
more candidate Instincts. Recon cannot publish knowledge or change the completed
run.

## BTN-23 — Operator review and promotion workflow

Allow a human to accept, edit then accept, or reject each candidate. Only this
workflow may promote a candidate into the Intel repository.

## BTN-24 — Deterministic retrieval and node-specific injection

Select accepted, active Instincts using explicit audience, tag, and
applicability rules, then assemble bounded role-specific context through the
BTN-26 context path.

## Deferred

- Operational usefulness feedback and confidence scoring, until real retrieval
  usage provides evidence.
- Semantic or embedding-based retrieval.
- Automatic promotion of Instincts into standards or architecture.
- Cross-project knowledge sharing.
