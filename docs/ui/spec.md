# Battalion Desktop UI Specification

**Version:** 1.0
**Status:** Implemented
**Applies to:** BTN-42, BTN-43, and BTN-56

This document describes the production PySide6 desktop client. RFC-0004 records
the accepted product direction, ADR-0022 selects PySide6 and Qt Widgets, and
ADR-0023 defines the durable human-action contract. If this document conflicts
with those decisions or the root `spec.md`, those sources take precedence.

## Purpose

Battalion is an engineering orchestrator, not a chat client. Its desktop UI
makes authoritative run, execution, worker, cost, artifact, interrupt, and
Intel evidence understandable while preserving the same application and graph
control paths used by the CLI.

The client optimizes for:

- project and ticket context before conversation;
- explicit missing, unknown, legacy, and failed evidence;
- progressive disclosure from run to node-attempt detail;
- durable state before transient live observations;
- typed human actions through existing authorities; and
- keyboard and assistive-technology access.

## Authority boundary

`battalion.desktop` is a presentation adapter over `battalion.application`.
It does not invoke LangGraph, mutate persisted `RunState`, write Intel records,
or implement a separate resume path. Queries and commands run through
`DesktopController`; application operations own validation, persistence,
worker supervision, and domain-error translation.

The desktop may display transient observations only after recovering the latest
durable state. A state-checkpoint observation triggers a fresh authoritative
query rather than becoming a second state store.

## Shipped destinations

### Work

Shows active or actionable runs: `not-started`, `in-progress`, `blocked`, and
`awaiting-human`. Runs are grouped by ticket. Selecting a run reveals its
durable summary, worker state, and ordered node attempts.

The Work destination also exposes human actions for the selected run:

- resolve the latest unresolved interrupt and resume through the canonical
  graph path;
- queue a Correction for Driver RED, Driver GREEN, or Refactorer; and
- queue a Design decision for Architect.

Actions require an actor. Intervention text is bounded, can be submitted only
while no worker is active, and is delivered once to the exact target's next
attempt. Mid-generation steering, Reviewer intervention, verdict override, and
manual checkpoint override are not provided.

### History

Shows terminal, earlier, legacy, missing, malformed, and inaccessible catalog
entries. Historical evidence remains inspectable without pretending missing
fields are known. Search and cross-run analytics remain BTN-44 work.

### Intel

Shows accepted Instincts and the immutable Recon candidate inbox. Pending
candidates may be promoted, edited then promoted, or rejected through the
canonical audited review workflow. Candidate evidence is never edited in place;
an edit-and-promote action creates accepted evidence with its decision record.

## Evidence inspector

Run inspection includes canonical identity, alias, ticket, status, phase,
legacy status, execution count, token totals, sourced monetary cost or explicit
unknown cost, and worker recovery information.

Node-attempt inspection includes available prompt, Git, bounded-context,
artifact, verification, tool, model, token, cost, timing, outcome, operator
summary, and revision evidence. Uncaptured or legacy categories render as
unavailable rather than disappearing.

## Human-action evidence

Interrupt resolution and interventions record actor, timestamp, target,
disposition, action identity, and resulting durable run state. Intervention
delivery is associated durably with a node-attempt identity before provider
generation. Recon decisions remain in the Intel review repositories rather than
being duplicated in run state.

## States and failure representation

The UI explicitly represents:

- loading, ready, and empty projects;
- missing, malformed, or inaccessible run state;
- active, completed, failed, cancelled, and crashed workers;
- known sourced cost and unknown or incomparable cost;
- legacy runs without newer evidence fields; and
- rejected human actions without speculative local mutation.

Errors remain visible in the project or live-status surface. They do not turn
into generic crashes or fabricated evidence.

## Visual system

The accepted design source is `ui/mockup/`. Production tokens are implemented
in `battalion.desktop.theme`:

| Token | Value | Use |
|---|---|---|
| Base | `#1a1b1e` | Window background |
| Raised | `#212226` | Panels, menus, controls |
| Sunken | `#17181b` | Trees, inputs, inspectors |
| Hover | `#26282c` | Pointer feedback |
| Border | `#2c2d31` | Quiet separation |
| Strong border | `#3a3c42` | Inputs and focus structure |
| Primary text | `#d8d9dc` | Main content |
| Secondary text | `#8b8d93` | Supporting content |
| Tertiary text | `#55575c` | Labels and disabled state |
| Accent | `#5b8dd6` | Selection and focus |
| Waiting | `#d6a95b` | Human attention |
| Success | `#6ba85b` | Successful state |
| Failure | `#d65b5b` | Errors and failed state |

IBM Plex Sans is the interface face. IBM Plex Mono is used for operational
labels, evidence, status, headers, and controls. Regular, Medium, and SemiBold
faces are bundled under the SIL Open Font License, so runtime rendering does
not depend on system installation or network access. Geometry uses compact
spacing, one-pixel borders, and two-pixel corner radii.

The supplied Battalion mark appears in the top bar. The supplied favicon is the
window and Windows executable icon.

## Accessibility and keyboard behavior

All destinations, evidence surfaces, identity fields, action inputs, and action
buttons have explicit accessible names. Standard Qt focus traversal reaches
every interactive destination and control. Selection works with the keyboard,
and `Ctrl+R` refreshes authoritative state. Disabled controls remain visible
until their preconditions are satisfied.

Color is not the sole carrier of state: text labels, tree columns, status text,
and enabled state also communicate meaning.

## Distribution

The release is split into sibling standalone distributions:

- the lightweight Qt desktop excludes graph, LangGraph, LiteLLM, and pytest;
- the worker contains graph/provider execution and excludes pytest.

Desktop assets are Python package data and explicit Nuitka data files. The
desktop locates `worker/worker_entry.dist/BattalionWorker.exe` beside its own
distribution. Source execution continues to launch `python -m
battalion.workers`. Both builds emit XML compilation reports.

## Explicitly not shipped

- global history search and model-role analytics (BTN-44);
- multiple projects in one window;
- settings and provider configuration screens;
- graph zoom, pan, animation, or arbitrary transition control;
- replay that re-executes or simulates a run;
- generic chat, IDE, terminal, or Git-client behavior; and
- automatic model routing or effectiveness recommendations.
