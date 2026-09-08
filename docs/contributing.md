# Contributor setup

Source development uses an editable install. End users and release-gate UAT
must instead follow [Getting Started](getting-started.md) with an identified
wheel/ZIP so a checkout cannot hide missing runtime assets.

Use Python 3.11+ and Git. In PowerShell, from your chosen development directory:

```powershell
git clone https://github.com/lrburkholder/battalion.git
Set-Location battalion
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest
```

For desktop development only, install `.[desktop,dev]` instead; that adds the
Qt and Nuitka build dependencies. Follow the repository's `AGENTS.md`, canonical
ticket criteria, and [implementation plan](../plan.md). Unit tests must not
require provider credentials or real provider calls. Consult
[Releases and distribution](release.md) for maintainer packaging and tagging;
neither an editable install nor a test pass publishes an end-user artifact.

## Maintaining tests

Shared builders live in `tests/support`, with separate ownership:

- `state.py`: validated RunState construction, independent per-role model
  configuration, and real persisted checkpoints.
- `execution.py`: node attempts, interrupts, and Reviewer process results.
- `responses.py`: completion envelopes and file/no-change responses.
- `graph.py`: hermetic role-runner wiring, recorded role sequences, checkpoint
  routing, explicit recursion bounds, and lifecycle-event callbacks.
- `cli.py`: paused-run scenarios shared by CLI commands and status inspection.
- `desktop.py`: desktop inspection scenarios, a recording controller, and one
  session-scoped QApplication fixture for the optional Qt tests.

Supply the content relevant to the test explicitly. State and execution builders
validate through production models and copy nested inputs to prevent shared
mutable evidence. Response builders preserve deliberately invalid payloads so
negative cases exercise production parsing. Keep semantic handoff data local;
the shared RunState scaffold must not freeze the shape of future typed contracts.
Unknown state, node-execution, and interrupt overrides raise `TypeError` using
the production model's field names. Process-result construction also rejects
unknown keywords through its dataclass constructor. This prevents misspelled
fixture inputs from being silently ignored without changing production schemas.

Use pytest fixtures for resource setup and teardown, such as temporary projects
or application lifetimes. Keep local helpers when they name a domain scenario
(for example, a Refactorer with successful GREEN artifacts). Use real persisted
files when testing serialization, restart, or recovery; a mocked save does not
demonstrate durability. Import helpers from their owning support module, never
from `conftest.py`. The latter retains only production initialization; it preserves
the existing graph-first import order pending a separate production import-cycle
cleanup. Tests of model schemas should continue constructing raw production
models when defaults or required fields are the behavior under test.

Remove a test only when another assertion covers its distinct failure mode.
Parameterization can reduce repeated code while preserving independently
reported cases; reducing the displayed case count is not itself a goal.
Assert concrete failure types rather than accepting every exception, and retain
end-to-end coverage through real application/graph and persistence boundaries.
The authoritative complete suite remains `python -m pytest tests/ -q`.

Large suites are divided by responsibility. `test_graph.py` covers graph
structure, checkpoint routing, and interrupts; `test_graph_role_results.py`,
`test_graph_resume.py`, and `test_graph_context.py` own their respective boundaries.
`test_cli.py` covers run/resume and configuration, with admission, status, and
tracing in the corresponding `test_cli_*.py` modules. `test_desktop.py` covers
window actions and accessibility; presentation, admission, recovery, and
packaging live in separate `test_desktop_*.py` modules. Keep acceptance scenarios
in `test_acceptance.py` exercising real nodes, subprocesses, and persistence.
A focused run of one former monolithic filename no longer covers its whole
subsystem; use the complete-suite command before handoff.

Prompt contract tests normalize whitespace before checking required wording.
Formatting changes should not fail authority or output-format checks; changes
to the actual instructions still require reviewing those assertions. These
static checks do not demonstrate model compliance with the instructions.

### Retained scenario and patch inventory (BTN-169)

The consolidation keeps the following local construction intentionally. These
helpers describe test inputs; they are not alternate copies of the shared
serialization or graph wiring code.

| Retained construction | Reason and ownership |
| --- | --- |
| Role-local `make_state` wrappers | Architect, Driver, Reviewer, and Refactorer tests set the role/phase and relevant schema/scope; common state construction delegates to `support.state`. |
| Application, worker, identity, observation, and desktop-query state wrappers | Keep canonical/legacy identity, lifecycle, scope, and budget choices visible in their domain; delegate model construction to `support.state`. |
| Persistence, interrupt, cost, notification, and Intel state wrappers | Preserve the particular saved specification, budget usage, interrupt, or role applicability being exercised. |
| Recon, Tactician, and Driver result payloads | Keep candidate evidence and recommendation/result semantics local; completion envelopes delegate to `support.responses`. |
| Model/schema fixtures | `test_models.py` and typed-contract tests construct production models directly because required fields, defaults, and validation are the subject. |
| Artifact and execution scenarios | Refactorer GREEN-artifact provenance and desktop inspection evidence have distinct authority/display inputs. Keep those fields explicit; use shared node-execution construction. Execution-record tests retain real file-producing stubs and distinct model identities so actual capture/hashing is exercised. |
| Side-effect, admission, and recovery records | Domain-specific attempts, receipts, authoritative evidence, and crash stages are intentionally explicit rather than hidden in a universal RunState fixture. |

Remaining direct patches have specific integration purposes:

- CLI patches the application execution collaborators to inspect command/config
  forwarding; graph scenarios centralize role-runner patches in `support.graph`.
- Graph wrapper tests replace `build_graph` specifically to inspect preservation
  of caller-supplied state and resume metadata without executing role nodes.
- Acceptance and Reviewer-execution tests inject provider/process outcomes into
  real nodes. Execution-record fixtures reuse the shared patch boundary while
  writing actual artifacts. Pure callback seams use their existing injection
  parameters rather than replacing internal implementations.
- Scope tests spy on write-tool construction to verify the authority boundary.
- Crash-recovery and repository tests interrupt the actual persistence/write
  seam to exercise partial writes and restart behavior.
- Worker, setup, build, and integration tests replace process, connectivity,
  transport, GitHub, or UI-opening collaborators to remain credential-free and
  prevent unintended external side effects. Packaging tests deliberately vary
  executable/frozen-runtime detection because that seam is their subject.

BTN-130 retains ownership of CI grouping; CI still discovers the complete
`tests/` tree. BTN-161's typed-contract migration is not constrained by these
builders: semantic handoff data remains local. The pre-existing production
import cycle documented in `conftest.py` is separate from this test-only change.
