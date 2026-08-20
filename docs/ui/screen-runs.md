# Desktop Work, History, and Intel Screen Contract

**Version:** 1.0
**Status:** Implemented

This document maps the production Qt Widgets screen rather than the earlier
conceptual Runs Hub. It is intentionally concrete about what is present today.

## Stable shell

```text
+--------------------------------------------------------------------------+
| [mark] battalion | project <name>                                        |
+--------------------------------------------------------------------------+
| authoritative project status                                             |
+------------+-------------------------------------------------------------+
| Work       | destination content                                         |
| History    |                                                             |
| Intel      |                                                             |
+------------+-------------------------------------------------------------+
| live connection, recovery, or human-action status                         |
+--------------------------------------------------------------------------+
```

The menu bar provides **Project → Refresh authoritative state** (`Ctrl+R`).
The top bar identifies the application and current project. Primary navigation
is persistent, and changing destination does not create another project or run
context.

## Work destination

```text
+----------------------+----------------------+-----------------------------+
| Runs                 | Execution map        | Inspector                   |
| Ticket / run         | Attempt / role /     | Selected run or attempt     |
| Status / phase       | phase / outcome      | evidence                    |
+----------------------+----------------------+-----------------------------+
| Human actions: actor, resolution/resume, intent, target, context, queue   |
+----------------------------------------------------------------------------+
```

### Runs

Active and actionable runs are grouped by ticket. Each selectable run shows its
display label, durable status, and phase. The parent ticket rows organize the
list and are not actions.

### Execution map

The current production map is an ordered tree of durable node attempts, not a
free-form animated graph. Columns show attempt number, role, phase, and outcome.
Selecting an attempt updates the Inspector. Zoom, pan, graph-edge interaction,
and replay are future possibilities, not shipped behavior.

### Inspector

The read-only monospaced Inspector renders the selected run summary or complete
node-attempt evidence. It explicitly calls out legacy or unavailable evidence.
Artifact references and provenance are displayed as evidence; the client does
not act as a general file editor.

### Human actions

The panel is visible and preserves context even when an action is unavailable.
Buttons become enabled only when the selected run and durable state satisfy the
operation's preconditions.

- **Resolve and resume** is available only for `awaiting-human` runs.
- **Queue for next attempt** accepts only the target combinations defined in
  ADR-0023 and requires the run to have no active worker.
- Actor, resolution, intent, target, and intervention fields have accessible
  names and participate in standard focus traversal.

## History destination

History uses the same Runs → Execution map → Inspector structure without the
human-action panel. It includes terminal and unavailable entries so loss or
corruption is visible. History search and analytics are not yet present.

## Intel destination

```text
+--------------------------------+------------------------------------------+
| Library                        | Inspector                                |
| Accepted Intel                 | Selected Instinct or candidate evidence  |
| Recon candidates + lifecycle  |                                          |
+--------------------------------+------------------------------------------+
| Review actor | Promote | Edit and promote | Reject                        |
+----------------------------------------------------------------------------+
```

Accepted Instincts and persisted Recon candidates are separate groups. The
lifecycle column shows pending or recorded disposition. Review buttons are
enabled only for a pending candidate. The Inspector remains read-only; edited
promotion text is collected in a modal prompt and passed to the canonical
review operation.

## Loading, empty, and failure states

- **Loading:** the project-status line announces authoritative loading while
  the last durable layout remains stable.
- **Empty:** trees contain explicit non-selectable empty rows.
- **Unavailable run:** the catalog entry remains visible and its limitation is
  rendered in the Inspector.
- **Project error:** the status line identifies the inaccessible project and
  clears stale tree content.
- **Action rejection:** the live-status line reports the domain failure without
  applying an optimistic local mutation.
- **Worker crash:** durable worker metadata is reconciled and recovery status is
  shown with the run.

## Visual language

The production screen follows `ui/mockup/battalion-runs-hub-mockup.html` while
retaining the implemented information architecture:

- IBM Plex Sans for interface content;
- IBM Plex Mono for evidence, controls, headers, and status;
- base `#1a1b1e`, raised `#212226`, and sunken `#17181b` surfaces;
- quiet `#2c2d31` and strong `#3a3c42` borders;
- `#d8d9dc`, `#8b8d93`, and `#55575c` text hierarchy;
- `#5b8dd6` selection and focus accent;
- two-pixel radii and compact spacing; and
- visible hover, focus, selected, pressed, and disabled control states.

The brand mark appears in the top bar. The same supplied icon is used for the
window and packaged Windows application. Font and icon assets live under
`battalion/desktop/assets` and are included in installed and frozen builds.

## Accessibility contract

Every navigation destination, run/execution tree, inspector, action field, and
button exposes a meaningful accessible name. Keyboard focus reaches each
destination and enabled action. State is always available as text; palette
differences supplement rather than replace labels.

The UI must continue to preserve these guarantees when its layout evolves:

1. presentation never bypasses `battalion.application`;
2. durable recovery precedes transient observation;
3. missing evidence remains explicit;
4. disabled actions remain understandable;
5. human-action authority is not broadened by visual affordances; and
6. presentation-only changes must not require recompiling the worker runtime.
