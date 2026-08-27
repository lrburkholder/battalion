# Desktop UI UAT plan

**Status:** Draft for maintainer review; not a shipped product contract.

## Purpose

Verify the production PySide6 desktop client as an operator-facing
presentation over Battalion's durable application state. This plan checks
observable behavior, accessibility, recovery, and authority boundaries; it
does not treat the desktop as a second way to edit graph state.

## Preconditions

- Complete the CLI UAT setup in a disposable project, retaining at least one
  run with node-attempt evidence and one `awaiting-human` run.
- Install the candidate with the desktop extra:

  ```powershell
  python -m pip install -e "C:\src\battalion[desktop,dev]"
  battalion-desktop --project <PROJECT_PATH>
  ```

- Use the same project path for every restart/recovery check.
- Record the operating system, display scaling, Python version, and whether
  the source-mode client or packaged desktop distribution was used.

## 1. Launch, loading, and empty-project behavior

1. Launch `battalion-desktop --project <EMPTY_PROJECT_PATH>`.
2. Confirm the project-status surface reports the empty state rather than a
   blank or crashed window.
3. Confirm **Work**, **History**, and **Intel** are visible and keyboard
   reachable.
4. Use **Project -> Refresh authoritative state** and `Ctrl+R`.

Pass criteria:

- The client remains responsive while loading.
- The empty message explicitly says that no runs, accepted Intel, or Recon
  candidates exist.
- Refresh neither creates data nor changes the selected project.

## 2. Work view and durable evidence

1. Reopen the project containing the CLI UAT evidence.
2. In **Work**, select a ticket and then a run.
3. Inspect the run summary, execution map, and each available node attempt.
4. Select the Architect attempt and verify the Inspector exposes recorded
   phase, outcome, model, token/cost evidence, artifact reference, and
   provenance limitations when applicable.

Pass criteria:

- Work presents active/actionable runs grouped by ticket.
- The execution map is ordered and selection updates only the read-only
  Inspector.
- Missing or legacy evidence is labeled unavailable; it is not silently
  invented or hidden.

## 3. Interrupt resolution and resume

1. Select the CLI UAT run whose status is `awaiting-human`.
2. Confirm **Resolve and resume** is enabled only for that selected run.
3. Confirm the Actor and enter a meaningful interrupt resolution.
4. Choose **Resolve and resume**.
5. Observe the live-status text, then refresh authoritative state.

Pass criteria:

- The UI records the resolution and starts the same resume path used by the
  CLI.
- The refreshed run records the human action and either advances or returns to
  a documented interrupt when the provider remains unavailable.
- Selecting a non-paused run leaves the resume control disabled.

## 4. Next-attempt intervention controls

Use a non-running, actionable run.

1. Select **Correction**. Verify the targets are Driver RED, Driver GREEN,
   and Refactorer.
2. Enter bounded corrective context and choose **Queue for next attempt**.
3. Refresh and inspect the run's human-action evidence.
4. Select **Design decision**. Verify Architect is its only available target.

Pass criteria:

- The UI never offers Reviewer as an intervention target.
- A queued action remains visibly queued until its exact target attempt
  consumes it.
- Attempting the action while a worker is active yields a visible rejection;
  the UI does not optimistically claim success.

## 5. History, restart, and worker recovery

1. In **History**, inspect a terminal run and any unreadable or incomplete
   catalog entry available in the test project.
2. Start or resume a worker-backed run, then close and reopen the desktop
   client while it is active.
3. Refresh after the worker reaches a durable checkpoint or exits.

Pass criteria:

- History preserves terminal and unavailable entries with explicit
  limitations.
- The restarted UI reloads durable state before later live observations.
- A crashed worker is shown as crashed/recoverable when state exists; the run
  identity and durable progress remain available.

## 6. Intel review (prepared-fixture check)

This check requires a project that already contains one pending Recon candidate
and, ideally, one accepted Instinct. Do not fabricate candidate state by
editing files by hand.

1. Open **Intel** and inspect accepted Intel and Recon candidates separately.
2. Select a pending candidate and confirm its immutable evidence is visible.
3. Confirm the review actor, then exercise **Promote**, **Edit and promote**,
   and **Reject** on separate prepared candidates.
4. Refresh after every action.

Pass criteria:

- Only pending candidates expose review controls.
- Each decision is durable and cannot be applied twice.
- Edit-and-promote produces decision-backed accepted evidence rather than
  modifying the candidate in place.

## 7. Accessibility and visual checks

1. Navigate all destinations, trees, controls, and enabled actions by keyboard.
2. Verify accessible labels for navigation, runs, execution evidence, Actor,
   resolution, intervention intent/target/text, and action buttons using the
   platform accessibility inspector where available.
3. Verify focus, selection, disabled controls, status text, and error states
   remain understandable without relying on color alone.
4. Capture Work, History, and Intel screenshots at the project display scale.

Pass criteria:

- Keyboard focus reaches every enabled action in a sensible order.
- Disabled actions remain visible and explainable through surrounding state.
- Text, layout, and evidence panes remain legible without clipped controls or
  unreadable contrast.

## Evidence to retain

Retain screenshots for Work, History, and Intel; the project-state snapshot;
worker status evidence; and a short log of every action, result, and observed
limitation. Redact project paths, run specifications, provider endpoints, and
credentials before sharing outside the local review.
