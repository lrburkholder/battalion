# Guardian Role Planning Brief

**Status:** Draft
**Ticket:** BTN-47
**Target:** Post-v2 architecture
**Related:** `philosophy.md`, `spec.md`, ADR-0007, ADR-0009, BTN-8, BTN-12

## Question

Does severity-based review require a new Guardian role, or should it be an
explicit policy within the existing Reviewer and interrupt contracts?

A new role is justified only if it has a distinct responsibility and evidence
boundary. Renaming a stricter Reviewer or adding an opaque approval gate would
increase graph complexity without improving human understanding.

## Candidate responsibility

Guardian may evaluate explicitly classified high-impact changes against
additional safety, security, privacy, migration, or operational criteria. The
planning work must first define which repository evidence selects those checks
and who owns the severity classification.

Guardian must not silently expand scope, replace the Reviewer, waive tests,
approve architectural changes for the human, or turn uncertain model judgment
into an irreversible block.

## Required decisions

- Objective trigger inputs, severity taxonomy, and false-positive handling.
- Whether classification is human-authored, mechanically derived, model-
  proposed, or a visible combination of those sources.
- Distinct inputs, outputs, prompt contract, model policy, and write scope.
- Graph placement and behavior on pass, finding, uncertainty, failure, retry,
  and repeated rejection.
- Relationship to the six v1 interrupts, Reviewer checkpoints, manual
  checkpoints, human resolution, and resume targets.
- Durable evidence needed to explain every trigger and finding.
- Whether existing Reviewer specialization is sufficient and preferable.

## Evidence to gather

Evaluate representative low- and high-impact changes with a policy-only
Reviewer variant and a disposable Guardian workflow. Compare finding quality,
duplicate feedback, operator burden, latency, cost, explainability, and graph
failure modes. The evidence must include cases where no additional gate is
warranted.

## Deliverable

BTN-47 produces a decision-ready RFC that selects no new role, Reviewer policy
specialization, or a precisely bounded Guardian. Any accepted graph, role,
prompt, interrupt, state, UI, or test work must be split into follow-up tickets
and reconciled with `spec.md` and an ADR before implementation.

## Non-goals

- Implementing a Guardian node or changing graph transitions.
- Treating model-assigned severity as authoritative.
- Creating a generic security scanner or replacing focused mechanical tools.
