# Battalion repository guidance

## Purpose

Battalion is a human-directed SDLC orchestrator. It coordinates Architect,
Driver, Reviewer, and Refactorer nodes while preserving explicit interrupt
points, mechanical write scopes, and a durable execution record.

Optimize for human leverage, transparency, and maintainability—not autonomy for
its own sake. Read `philosophy.md` before proposing changes to roles, authority,
interrupt behavior, or the knowledge lifecycle.

## Sources of truth

Use repository artifacts in this order when they disagree:

1. `spec.md` for the shipped v1 contract and acceptance criteria.
2. Accepted ADRs and the ADR log in `plan.md` for architectural decisions.
3. Canonical GitHub Issues for ticket identity, scope, dependencies, and status.
4. Source code and tests for implemented behavior.
5. Draft RFCs and proposals under `docs/` for future work.
6. Conversation context.

Do not treat a draft RFC or backlog proposal as shipped behavior. Surface
conflicts instead of silently choosing one source.

## Architecture and boundaries

- Runtime flow: Architect -> Driver (RED) -> Reviewer -> Driver (GREEN) ->
  Reviewer -> Refactorer -> Reviewer -> done.
- `battalion/state/` owns the versioned state contract and persistence.
- `battalion/nodes/` owns role behavior. Prompts live in top-level `prompts/`.
- `battalion/scope/` owns structural write-scope enforcement.
- `battalion/interrupts/` owns the six v1 interrupt conditions and budget
  tracking.
- `battalion/graph.py` wires nodes and transitions; keep role policy out of the
  CLI.
- `battalion/application.py` owns transport-neutral run, resume, inspection,
  and human-authorized application operations shared by presentation clients.
- `battalion/cli.py` is a thin presentation adapter over the application
  boundary; it must not invoke LangGraph or mutate persisted state directly.

Apply IO-distance when introducing dependencies: keep application policy free
of filesystem, network, UI, framework, and transport details. Before adding an
abstraction or dependency, ask whether the standard library or an existing
dependency already solves the problem.

## Working agreements

- Preserve unrelated and uncommitted work. Never reset or rewrite user changes.
- Work from one `BTN-#` ticket at a time. Confirm its acceptance criteria and
  dependencies in its canonical GitHub Issue before implementation.
- Add or update tests with behavior changes. Prefer focused tests during
  iteration, then run the full suite before handoff.
- Keep Driver and Reviewer configured with different models.
- Treat edits to role definitions, node prompts, graph authority, write scopes,
  and interrupt semantics as architectural changes. Explain the impact and
  update the relevant ADR or specification.
- Keep prompts role-specific. Driver implements approved architecture; Reviewer
  checks correctness and specification compliance; Refactorer preserves
  behavior; Architect plans and records decisions.
- Never place API keys or secrets in tracked configuration, fixtures, logs, or
  documentation.
- Ticket status changes flow through locked GitHub Issue labels and
  `python scripts/sync_status.py`, which regenerates the "Delivered work"
  region of `docs/status.md` and the embedded copy inside `README.md`.
  Do not hand-edit those generated regions, and keep the authenticated
  `python scripts/sync_status.py --check` clean before handoff (ADR-0027).

## Setup and validation

Use Python 3.11 or newer from the repository root:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
```

Useful focused commands:

```bash
python -m pytest tests/test_graph.py -q
python -m pytest tests/test_cli.py -q
python -m battalion --help
```

When a full local suite would be slow or contend with local development
processes, push the branch and run `./scripts/run_ci.sh` instead. It dispatches
the on-demand GitHub Actions workflow against the current branch (or an
explicit `--branch`) and streams the result; it requires an authenticated
`gh` CLI. Pass a test path or `-k` expression for a remotely focused run.

For live LLM setup, use `python -m battalion setup`. Connectivity checks may
make network calls and require provider credentials; unit tests must not.

## Documentation hygiene

- Reconcile documentation as part of the ticket, not as deferred cleanup.
  Before handoff, compare `README.md`, `plan.md`, and relevant docs with the
  ticket status, implemented behavior, and test evidence.
- Update `README.md` for user-visible commands, configuration, architecture
  components, milestone status, or roadmap progress. Remove or correct stale
  status claims in the same change.
- Update the status and module/delivery descriptions in `plan.md` when shipped
  architecture or implementation progress changes.
- Update `spec.md` when the product contract changes.
- Record durable architecture decisions in an ADR; do not leave them only in a
  chat or implementation comment.
- Keep the canonical GitHub Issue synchronized with actual work: mark a ticket
  in progress when implementation begins, and mark it done only when every
  acceptance criterion has matching implementation and validation evidence.
- When accepted public documentation is added or newly referenced, update the
  explicit publication set in `scripts/build_pages.py` and its tests so GitHub
  Pages does not contain dead links. Validate the staged Pages output before
  handoff.
- Distinguish branch progress from shipped behavior. Documentation on a feature
  branch may describe work as in progress; only describe it as shipped after
  the backlog, tests, and merge state support that claim. GitHub Pages deploys
  from `main`, so do not claim the live site is updated before merge.
- Keep ticket IDs unique across GitHub Issues and proposals.
- Mark future-looking documents as Draft and avoid presenting them as current
  behavior.
- When docs and implementation disagree, state which one was corrected and the
  evidence used.

## Code review rules

- Flag any path that bypasses scoped write tools or broadens a node's authority.
- Flag graph transitions that can skip required RED, GREEN, refactor, review, or
  human-interrupt checkpoints.
- Flag malformed-state and provider failures that escape as generic crashes
  instead of the documented failure/interrupt behavior.
- Flag tests that make real provider calls or depend on developer credentials.
- Flag documentation that marks a ticket complete without matching canonical
  GitHub Issue and test evidence.
