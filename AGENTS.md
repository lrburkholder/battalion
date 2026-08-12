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
3. `backlog.json` for ticket identity, scope, dependencies, and status.
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
- `battalion/cli.py` is a thin adapter over reusable runtime functions.

Apply IO-distance when introducing dependencies: keep application policy free
of filesystem, network, UI, framework, and transport details. Before adding an
abstraction or dependency, ask whether the standard library or an existing
dependency already solves the problem.

## Working agreements

- Preserve unrelated and uncommitted work. Never reset or rewrite user changes.
- Work from one `BTN-#` ticket at a time. Confirm its acceptance criteria and
  dependencies in `backlog.json` before implementation.
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

## Setup and validation

Use Python 3.11 or newer from the repository root:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pip install langgraph  # temporary until declared in pyproject.toml
python -m pytest
```

Useful focused commands:

```bash
python -m pytest tests/test_graph.py -q
python -m pytest tests/test_cli.py -q
python -m battalion --help
```

For live LLM setup, use `python -m battalion setup`. Connectivity checks may
make network calls and require provider credentials; unit tests must not.

## Documentation hygiene

- Update `README.md` for user-visible commands, configuration, or status.
- Update `spec.md` when the product contract changes.
- Record durable architecture decisions in an ADR; do not leave them only in a
  chat or implementation comment.
- Keep ticket IDs unique across `backlog.json` and proposals.
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
- Flag documentation that marks a ticket complete without matching backlog and
  test evidence.
