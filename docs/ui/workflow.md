# Battalion Desktop Operator Workflows

**Version:** 1.0
**Status:** Implemented

These workflows describe the production BTN-42/43 desktop client. Every query
and action crosses `battalion.application`; the UI never becomes a second graph,
run-state, worker, or Intel authority.

## 1. Open or refresh a project

1. Launch `battalion-desktop --project <path>` or `python -m
   battalion.desktop --project <path>`.
2. The client loads project identity, the run catalog, durable run states,
   worker records, accepted Intel, and Recon candidates off the UI thread.
3. Work, History, and Intel update from one authoritative snapshot.
4. Use **Project → Refresh authoritative state** or `Ctrl+R` to reload.

An empty project says that no run or Intel evidence exists. Missing identity,
malformed state, and inaccessible paths are shown as typed visible failures.

## 2. Inspect active or actionable work

1. Open **Work**.
2. Choose a ticket, then a run.
3. Read the run summary in the Inspector.
4. Choose a node attempt from **Execution map** for detailed provenance,
   context, verification, artifacts, tool activity, model, tokens, and cost.

Selecting evidence never mutates the run. Missing evidence is labeled
unavailable.

## 3. Inspect run history

1. Open **History**.
2. Select a terminal, earlier, legacy, or unavailable catalog entry.
3. Inspect its run and node-attempt evidence as in Work.

Unreadable entries remain visible with their limitation. The UI does not hide
them or infer absent values.

## 4. Follow a live worker safely

1. Select a run that has worker metadata.
2. The controller reconnects to durable state before consuming later live
   observations.
3. The live-status line shows observation sequence, kind, and node when known.
4. A durable checkpoint observation causes a fresh authoritative refresh.

If a detached worker exits abnormally, the client reconstructs a crashed and,
when state exists, recoverable worker record. Restarting the UI does not lose
the canonical run identity or durable progress.

## 5. Resolve an interrupt and resume

1. In Work, select an `awaiting-human` run.
2. Confirm the **Actor** and enter an **Interrupt resolution**.
3. Choose **Resolve and resume**.
4. Battalion records the resolution against the latest unresolved interrupt,
   saves the resulting action evidence, and launches resume through the same
   application and graph path used by the CLI.
5. Success or rejection appears in the live-status line and authoritative state
   is refreshed.

The button is disabled for runs that are not awaiting human resolution.

## 6. Queue next-attempt context

1. Select an available Work run with no active worker.
2. Confirm the actor.
3. Choose **Correction** and Driver RED, Driver GREEN, or Refactorer; or choose
   **Design decision**, which targets Architect.
4. Enter bounded intervention text.
5. Choose **Queue for next attempt**.

The action is persisted separately from the ticket specification. The exact
target consumes it once at the next node-attempt entry, after durable
association and before provider generation. It never reaches Reviewer or an
already-running model call.

## 7. Review Recon candidates

1. Open **Intel** and expand **Recon candidates**.
2. Select a pending candidate and inspect its immutable evidence.
3. Confirm the review actor.
4. Choose one action:
   - **Promote** accepts the candidate as recorded;
   - **Edit and promote** requests a revised recommendation and accepts that
     revision; or
   - **Reject** records rejection without creating accepted Intel.
5. The canonical review workflow records the decision and refreshes Intel.

Already decided candidates cannot be reviewed again through these controls.

## 8. Audit model use and cost

1. Select a run and node attempt.
2. Inspect the recorded model identity and each LLM call's input/output tokens.
3. Read monetary cost with its source and currency when present.
4. Treat `unknown` as unknown; the UI never converts missing provider evidence
   to zero.

Cross-run filtering, aggregation, and comparative analytics are deferred to
BTN-44.

## 9. Build and assemble the desktop release

From the repository root:

```powershell
# Fast presentation-only build
& .\.venv\Scripts\python.exe .\scripts\build_desktop.py --component desktop

# Heavy graph/provider worker build
& .\.venv\Scripts\python.exe .\scripts\build_desktop.py --component worker

# Both, sequentially
& .\.venv\Scripts\python.exe .\scripts\build_desktop.py --component all
```

The desktop build bundles IBM Plex and the Battalion icons. The worker does not
contain PySide6. Native builds may be run in separate processes; rebuilding the
desktop for presentation changes does not require recompiling the worker.
