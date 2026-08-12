# Battalion v1 — Architecture Plan

## Status

The v1 architecture described here is implemented through BTN-15 and validated
by BTN-10. Future Recon and Intel work remains draft design under `docs/` and is
not part of the shipped v1 graph.

## Architecture overview

Battalion is a LangGraph `StateGraph` with four roles, Pydantic models as its
single state contract, LiteLLM as its model boundary, and Typer as its CLI. Run
state persists as local JSON. Each writing node receives tools constrained to
its declared paths, so write authority is established when the graph is built.

The successful execution path is:

```text
Architect
  -> Driver (RED)
  -> Reviewer (expects failure)
  -> Driver (GREEN)
  -> Reviewer (expects success)
  -> Refactorer
  -> Reviewer (expects success)
  -> Done
```

A rejection returns control to the node responsible for that checkpoint. An
interrupt pauses the run in an `awaiting-human` state with enough context to
resume through the same graph path.

## Module boundaries

```text
battalion/
  cli.py                  # Typer adapter: run, resume, status, setup
  config.py               # YAML, environment, and CLI configuration merge
  setup.py                # provider discovery and connectivity setup (BTN-15)
  graph.py                # graph construction, routing, pause, and resume
  progress.py             # CLI progress projection
  state/
    models.py             # versioned Pydantic state contract
    persistence.py        # local JSON state load/save
  nodes/
    architect.py
    driver.py             # RED and GREEN modes
    reviewer.py           # expected-outcome checkpoint review
    refactorer.py
    errors.py
  prompts/
    loader.py             # prompt loading and overrides
  scope/
    tool_binding.py       # per-node scoped write-tool factory
  interrupts/
    triggers.py           # six v1 interrupt checks
    budget.py             # per-run budget tracking
  llm/
    litellm_client.py     # per-node model configuration and invocation

prompts/                  # externalized role prompts
tests/                    # unit and end-to-end acceptance tests
```

Dependencies point toward application policy. The CLI, filesystem, network,
LiteLLM, and LangGraph wiring are boundary concerns; role intent and state
invariants should not depend on their concrete transport or persistence shapes.

## ADR log

### ADR-001: Pydantic owns state validation

Define the shared, versioned state contract as Pydantic models. This keeps
validation and Python types together and avoids a separate JSON-Schema-to-model
translation layer. The tradeoff is that Pydantic is a core dependency.

### ADR-002: Enforce write scope through tool binding

Construct each node's write tools at graph-build time using only its declared
paths. A node should never receive a tool capable of writing elsewhere.
Runtime scope violations remain a defense-in-depth interrupt and audit signal.

### ADR-003: Keep Typer as a thin CLI

Use Typer for `run`, `resume`, `status`, and setup commands. CLI handlers adapt
arguments and presentation to reusable runtime functions; they do not own graph
policy.

### ADR-004: Implement native Battalion roles

Architect, Driver, Reviewer, and Refactorer are native graph nodes, not wrappers
over earlier Regi or Copilot agents. Earlier material can inform behavior but
does not define runtime interfaces.

### ADR-005: Externalize role prompts

Store default role prompts in top-level `prompts/`. Node functions may accept
explicit prompt or prompt-directory overrides for testing and customization.
Missing or empty required prompts fail clearly instead of silently degrading.

Prompt text must mirror the node's mechanically enforced contract: Architect
emits Markdown plan content; file-producing roles emit complete file contents in
the `{"files": {...}}` JSON shape using paths relative to their bound `src/`
root; Reviewer emits only one normalized rejection-cause sentence. Prompts must
not claim access to tools, tests, repository context, or authority the node does
not actually receive.

### ADR-006: Split Driver into RED and GREEN modes

The Driver has explicit RED and GREEN modes with the same output shape. RED
produces failing tests; GREEN produces the smallest implementation that makes
the accepted tests pass. This creates independently reviewable checkpoints.

### ADR-007: Review against an expected outcome

Reviewer acceptance means the observed result matches the checkpoint's expected
result. RED expects failure; GREEN and REFACTOR expect success. A passing RED
test is therefore not automatically an acceptance.

### ADR-008: Give Refactorer Driver's implementation scope

Refactorer preserves behavior while improving implementation and uses the same
declared implementation scope as Driver. One scope declaration avoids duplicated
paths drifting apart. This shared scope does not grant architectural authority.

### ADR-009: Count rejection causes per checkpoint type

Track repeated Reviewer root causes independently for RED, GREEN, and REFACTOR
checkpoints. Similar wording at different phases must not create a false
"rejected twice" interrupt.

## Interrupt contract

The v1 graph recognizes six interrupt categories:

1. The same Reviewer root cause is rejected twice at the same checkpoint.
2. A node attempts an out-of-scope write.
3. The per-run budget is exceeded.
4. Work would modify a role definition.
5. Infrastructure or provider execution fails after retries.
6. A configured manual checkpoint is reached.

Interrupts are durable state transitions, not generic crashes or log messages.
Resume must continue from the recorded graph target rather than restarting the
ticket or bypassing a checkpoint.

## Delivery sequence

The v1 implementation landed in this dependency order:

1. State models and persistence (BTN-1).
2. Scoped tool binding (BTN-2).
3. LiteLLM boundary (BTN-3).
4. Architect, Driver, and Reviewer nodes (BTN-4 through BTN-6).
5. RED/GREEN, expected-outcome review, and Refactorer design corrections
   (BTN-11 through BTN-13).
6. Graph and interrupt wiring (BTN-7 and BTN-8).
7. CLI and end-to-end acceptance validation (BTN-9 and BTN-10).
8. Driver/Reviewer model diversity (BTN-14).
9. Guided provider and model setup (BTN-15).

Later backlog work must build on these contracts instead of introducing parallel
run, resume, persistence, or review paths.

## Risks and watch items

- Rejection-cause comparison depends on consistent, specific Reviewer output.
- Tool construction is a security boundary; tests should assert each node's
  exact authority.
- A single node can consume most of a run-level budget. Per-call reporting is
  future work, but must not change the v1 budget interrupt semantics.
- Role prompts evolve faster than node code. Prompt changes can still change
  behavior materially and should be reviewed as role-definition changes.
- The graph currently retains only `ticket_id` in `RunState`; the supplied
  specification is discarded and source context is not assembled for Driver or
  Refactorer. Prompt quality cannot compensate for missing task and code context.
- Recon is the canonical name for Battalion's knowledge-capture role; Learner
  refers only to its historical Regiment predecessor. Draft Recon and Intel
  ticket proposals must still be reconciled with `backlog.json` before
  implementation begins.
