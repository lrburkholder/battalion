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

The canonical decision records live in [`docs/adrs/`](docs/adrs/README.md).
The decisions implemented by the v1 architecture are:

| ADR | Decision |
| --- | --- |
| [ADR-0001](docs/adrs/adr0001.md) | Use Pydantic for state validation |
| [ADR-0002](docs/adrs/adr0002.md) | Enforce write scope through tool binding |
| [ADR-0003](docs/adrs/adr0003.md) | Keep Typer as a thin CLI |
| [ADR-0004](docs/adrs/adr0004.md) | Implement native Battalion roles |
| [ADR-0005](docs/adrs/adr0005.md) | Externalize role prompts |
| [ADR-0006](docs/adrs/adr0006.md) | Split Driver into RED and GREEN modes |
| [ADR-0007](docs/adrs/adr0007.md) | Review against an expected outcome |
| [ADR-0008](docs/adrs/adr0008.md) | Give Refactorer Driver's implementation scope |
| [ADR-0009](docs/adrs/adr0009.md) | Count rejection causes per checkpoint type |

Future-facing knowledge-system records are indexed separately in the same
directory and are not part of the shipped v1 graph.

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
- BTN-26 persists the supplied specification in `RunState` and assembles
  deterministic, bounded context for Architect, Driver RED/GREEN, and
  Refactorer through one canonical context path.
- Recon is the canonical name for Battalion's knowledge-capture role; Learner
  refers only to its historical Regiment predecessor. Draft Recon and Intel
  ticket proposals must still be reconciled with `backlog.json` before
  implementation begins.
