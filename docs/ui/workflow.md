# Battalion Desktop Workflow

This guide explains how to use the Battalion desktop application after your
project and models have been configured. For installation and first-run setup,
start with [Getting Started](../getting-started.md).

The desktop application shows and acts on the same saved Battalion Runs used by
the CLI. It does not maintain a separate copy of Run state. When the UI refreshes,
the saved project state is authoritative.

If a Run fails, a worker stops unexpectedly, or you are unsure whether a resume
is safe, use [Troubleshooting and recovery](../troubleshooting.md) before trying
again.

## Open a project

Launch Battalion with the project you want to inspect:

```powershell
battalion-desktop --project C:\path\to\project
```

You can also launch the Python module directly when using a Python installation:

```powershell
python -m battalion.desktop --project C:\path\to\project
```

Battalion loads the project's saved Runs, worker state, accepted Intel, and Recon
candidates. The main views are:

| View | What it is for |
| --- | --- |
| **Work** | Runs that are active or need attention |
| **History** | Completed and other terminal Runs |
| **Intel** | Accepted Intel and Recon candidates |

Use **Project → Refresh authoritative state** or `Ctrl+R` whenever you want to
reload the latest saved state from disk.

If the project has no Battalion history yet, the views may simply be empty.
Missing or unreadable state is shown as an error rather than silently ignored.

## Inspect a Run

Open **Work** or **History**, then select a Run.

The Inspector shows the Run's current state and its execution history. Select a
node attempt in the **Execution map** to see the available evidence for that
attempt, including things such as:

- what role ran;
- which model was used;
- input and output token counts;
- known monetary cost;
- artifacts and tool activity;
- context and provenance; and
- test or review evidence.

Inspecting evidence is read-only. If Battalion does not have a value, the UI
shows it as unavailable rather than guessing one.

## Follow a running worker

When a Run is executing through a desktop worker, Battalion can show live
observations as they arrive. Durable Run state still takes priority over the
live stream.

If a durable checkpoint is saved, the UI refreshes the Run from authoritative
state. If the desktop is restarted, it reconnects using the saved Run and worker
records rather than creating a new Run.

Closing the desktop does **not** guarantee that a detached worker has stopped.
Do not start another worker or resume the same Run until you have confirmed that
no existing process is still working on it. See
[worker recovery](../troubleshooting.md#worker-recovery) if the UI stops updating
or a worker exits unexpectedly.

## Resolve a human checkpoint

When a Run has `Status: awaiting-human`, Battalion is waiting for a decision
rather than treating the pause as a generic failure.

To continue:

1. Select the Run in **Work**.
2. Read the interrupt reason and relevant evidence.
3. Confirm the **Actor** who is making the decision.
4. Enter an **Interrupt resolution** that explains the decision.
5. Choose **Resolve and resume**.

Battalion saves that human decision with the Run and resumes from the recorded
checkpoint. The resulting progress or rejection then appears in the UI.

**Resolve and resume** is disabled when the selected Run is not waiting for a
human decision.

A resume is an execution action, not a diagnostic command. It can call models,
run tests, and change files within the Run's existing authority. If the saved
recovery state is unclear, inspect it before resuming.

## Give the next attempt additional guidance

Sometimes the correct response is not to resume immediately, but to give a
specific role additional context for its next attempt.

With a Work Run selected and no worker currently active, you can queue either:

- a **Correction** for Driver RED, Driver GREEN, or Refactorer; or
- a **Design decision** for Architect.

Confirm the Actor, enter the bounded guidance, and choose **Queue for next
attempt**.

The guidance is saved separately from the original ticket/specification and is
delivered only to the selected role's next attempt. It does not modify the
original requirements, reach Reviewer, or alter a model call that is already in
progress.

If the task really requires broader authority or a changed specification, do
not use an intervention to smuggle that change through. Make the appropriate
human architecture or work-item decision instead.

## Review Recon candidates

Battalion's Recon process can propose candidate **Instincts**: lessons from past
work that may be useful in future Runs. Candidates are not automatically trusted.
A human must review them.

Open **Intel**, expand **Recon candidates**, and select a pending candidate. Read
its recommendation and evidence, confirm the reviewing Actor, then choose one of:

- **Promote** — accept it as written;
- **Edit and promote** — revise the recommendation and accept the revision; or
- **Reject** — record that it should not become accepted Intel.

The decision is saved separately from the original candidate. Once a candidate
has been decided, these controls do not allow it to be reviewed again as though
it were new.

## Understand model and cost evidence

For a selected node attempt, Battalion shows the recorded model and available
LLM-call evidence.

Token counts are recorded when available. Monetary cost is shown with its
currency and source when Battalion has that evidence.

**Unknown cost means unknown.** Battalion does not turn missing provider cost
information into `$0`.

Broader cross-Run analytics and comparison are future work; the current desktop
focuses on evidence for individual Runs and attempts.

## What the desktop does not change

Using the desktop does not bypass Battalion's normal controls. The same basic
rules still apply:

- role write scopes remain enforced;
- Reviewer does not gain project write access;
- human checkpoints remain durable decisions;
- queued guidance is limited to its selected future attempt;
- saved Run state remains authoritative; and
- unknown evidence is not silently invented.

The desktop is another way to operate Battalion, not a separate workflow engine.
CLI and desktop operations act through the same Battalion application boundary.

## If something goes wrong

Do not repeatedly press resume or restart workers until the saved state is
understood. In particular, a worker process finishing successfully does not
necessarily mean the Run itself is complete; it may have stopped at a human
checkpoint.

Use [Troubleshooting and recovery](../troubleshooting.md) for:

- provider or model failures;
- malformed role output;
- Reviewer test failures;
- write-scope or authority blocks;
- interrupted resume;
- stale or crashed workers; and
- missing or unreadable state.

For information about what the desktop displays, stores, or may send to model
providers, see [Data handling and trust boundaries](../data-handling.md).

Source contributors and maintainers looking for desktop build or packaging
instructions should use [Contributor guidance](../contributing.md) and
[Releases and distribution](../release.md); those tasks are intentionally
separate from this operator workflow.