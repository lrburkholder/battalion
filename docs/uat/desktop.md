# Desktop UI UAT plan

**Status:** Prepared for operator review under BTN-170; record reviewer, date,
script revision, and approval decision before formal execution. No final live
acceptance is claimed. [BTN-132](https://github.com/lrburkholder/battalion/issues/206)
owns that acceptance after BTN-173's main-based candidate and BTN-129's CLI gate.

## Purpose

Verify the production PySide6 desktop client as an operator-facing
presentation over Battalion's durable application state. This plan checks
observable behavior, accessibility, recovery, and authority boundaries; it
does not treat the desktop as a second way to edit graph state.

## Preconditions

- Complete the CLI UAT setup in a disposable project, retaining at least one
  run with node-attempt evidence and one `awaiting-human` run.
- Install the exact candidate **Windows x64 ZIP** following
  [Getting Started](../getting-started.md), including checksum/provenance checks,
  executable layout, and worker prompt smoke. Do not install the desktop extra
  from a checkout or substitute a source-mode client for a failing ZIP.

  ```powershell
  & $DesktopExe --project $Project
  ```

- Use the same project path for every restart/recovery check.
- Record operating system, display scaling, PowerShell/Python versions, and
  the exact ZIP filename, expected/actual SHA-256, package version, source
  commit, build/run URL, and metadata filename. Retain the paired CLI wheel
  identity/checksum and guide/script revision too. CLI and desktop must match
  the same candidate; identify every remediation baseline and required rerun.
- Record signing/SmartScreen warnings accurately. No protection bypass is a
  documented prerequisite. A blocked organizational approval is not a pass.
- Preserve the entire sibling desktop/worker layout. `$DesktopExe`, `$Project`,
  `$Python`, and `$RunId` are established by Getting Started.

## 0. Clean-environment, documentation-only pass

A new operator follows the published Getting Started guide, including CLI
configuration and ZIP onboarding, using only the artifact handoff and published
documentation. Use no source checkout, developer installation, or maintainer
assistance. Record every required undocumented step as a defect with guide
section, exact candidate, expected/actual result, and sanitized evidence. If
assistance is necessary, record the failure and rerun cleanly after correction.

Inspect the guide-created terminal Run in History. Prepare a fresh disposable
project using guide sections 3–4, start its Run with `--checkpoint driver`, and
leave it paused for Work/resume; relaunch the desktop with that new `$Project`.
Compare UUID and durable evidence
between CLI and desktop. Do not call read-only inspection or prompt-smoke
success a full packaged execution pass.

The guide documents a current frozen-worker pytest boundary limitation. Verify
and retain the first packaged Reviewer process result in scenario 3. A failure
must become a BTN-132 remediation finding with a new artifact baseline and
rerun, not a source-mode workaround. Controlled worker crash and Intel fixtures
below are separate supplied scenarios; they do not excuse gaps in onboarding.

## 1. Launch, loading, and empty-project behavior

1. Create a new empty disposable directory and launch the packaged executable:

   ```powershell
   $EmptyProject = Join-Path $Lab ('empty-project-' + [guid]::NewGuid().ToString())
   New-Item -ItemType Directory -Path $EmptyProject | Out-Null
   & $DesktopExe --project $EmptyProject
   ```

   Then return to `$Project` for scenarios with saved evidence.
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
- For a runnable candidate, the packaged worker traverses all remaining review
  checkpoints and reaches `done`; inspect CLI JSON status to retain Reviewer
  command, classification, test counts, and cleanup evidence. The known frozen
  pytest failure is a failed release-gate check, even if resume authorization
  was recorded correctly.

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

Retain the artifact/provenance record, script approval, documentation-only defect
log, per-scenario pass/fail/blocked disposition, screenshots for Work, History,
and Intel; the project-state snapshot;
worker status evidence; and a short log of every action, result, and observed
limitation. Redact project paths, run specifications, provider endpoints, and
credentials before sharing outside the local review.
