# Battalion v1 — Architecture Plan

## Status

The v1 execution architecture is complete and validated. BTN-16 and BTN-19
through BTN-28 add durable cost and execution evidence, the human-audited Recon
and Intel lifecycle, deterministic context assembly, caller-owned run
configuration, and project-layout-aware scope enforcement. RFC-0004 (BTN-29)
defines the accepted desktop operator direction. BTN-30 implements the shared
application command/query boundary, BTN-31 adds detached per-run worker
supervision, and BTN-32 separates generated canonical run/project identity from
display aliases with a compatible project-local catalog. BTN-33 extends the
durable execution contract with bounded operator, prompt, revision, and context
evidence. Later desktop tickets remain unshipped backlog work.

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
  application.py          # shared typed commands, queries, and domain failures
  identity.py             # canonical UUIDs, project markers, and run catalogs
  workers.py              # detached per-run process supervision and recovery
  cli.py                  # Typer adapter: run, resume, status, setup
  config.py               # YAML, environment, and CLI configuration merge
  context.py              # bounded role context and Instinct assembly
  execution.py            # durable node evidence, provenance, and cost views
  setup.py                # provider discovery and connectivity setup (BTN-15)
  graph.py                # graph construction, routing, pause, and resume
  progress.py             # CLI progress projection
  intel/
    models.py             # candidate and accepted Instinct contracts
    repository.py         # immutable accepted-Instinct storage
    retrieval.py          # deterministic active-Instinct selection
    review.py              # audited operator review and promotion
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

Dependencies point toward application policy. The CLI delegates run, resume,
inspection, costs, and persistence to `battalion.application`; future graphical
clients must use the same boundary. Filesystem, network, LiteLLM, and LangGraph
wiring remain boundary concerns, while role intent and state invariants do not
depend on a presentation transport.

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
| [ADR-0013](docs/adrs/adr0013.md) | Bind write tools to project layout phases |
| [ADR-0014](docs/adrs/adr0014.md) | Persist a bounded execution record in RunState |
| [ADR-0015](docs/adrs/adr0015.md) | Keep Recon outside the completed execution graph |
| [ADR-0016](docs/adrs/adr0016.md) | Make Instinct promotion an audited human boundary |
| [ADR-0018](docs/adrs/adr0018.md) | Use literal, inspectable Instinct retrieval |
| [ADR-0019](docs/adrs/adr0019.md) | Supervise active runs with detached per-run workers |
| [ADR-0020](docs/adrs/adr0020.md) | Separate canonical run and project identity from display names |

Knowledge-system records are indexed separately in the same directory. BTN-24
adds accepted Instinct retrieval to role context without adding Recon or human
promotion to the execution graph.

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
10. Durable cost, execution, and artifact provenance (BTN-16 and BTN-19).
11. Recon and Intel contracts, persistence, generation, promotion, and
    deterministic retrieval (BTN-20 through BTN-24).
12. Role prompts, bounded execution context, caller-owned run configuration,
    and project-layout-aware scopes (BTN-25 through BTN-28).
13. Desktop operator architecture and follow-up decomposition (BTN-29).
14. Shared application commands and queries (BTN-30).
15. Detached active-run worker supervision and durable recovery evidence
    (BTN-31).
16. Canonical UUID run identity, project markers, display aliases, and legacy
    catalog compatibility (BTN-32).
17. Bounded operator summaries and revision evidence (BTN-33).

Later backlog work must build on these contracts instead of introducing parallel
run, resume, persistence, or review paths.

## Future architecture planning

BTN-45 through BTN-48 turn the README's post-v2 directions into bounded
architecture work for a possible Specifier role, a permissioned plugin model,
severity-based review and a possible Guardian role, and carefully constrained
self-modification research. Their planning briefs live under `docs/future/`.

These tickets produce decision-ready RFCs and follow-up decomposition only.
They do not authorize new roles, graph transitions, interrupt behavior,
permissions, integrations, or self-editing. Any accepted change to those
surfaces must reconcile `spec.md`, record durable decisions in ADRs, preserve
human approval boundaries, and receive separate implementation tickets.

## Risks and watch items

- Rejection-cause comparison depends on consistent, specific Reviewer output.
- Tool construction is a security boundary; tests should assert each node's
  exact authority.
- A single node can consume most of a run-level budget. Per-call cost evidence
  is now durable, but it intentionally does not change v1 budget interrupts.
- Role prompts evolve faster than node code. Prompt changes can still change
  behavior materially and should be reviewed as role-definition changes.
- BTN-26 persists the supplied specification in `RunState` and assembles
  deterministic, bounded context for Architect, Driver RED/GREEN, and
  Refactorer through one canonical context path.
- BTN-24 selects active accepted Instincts with literal audience,
  applicability, and tag rules, then injects whole identified entries through
  that same bounded context path for every execution role.
- Recon is the canonical name for Battalion's knowledge-capture role; Learner
  refers only to its historical Regiment predecessor. Candidate generation and
  operator promotion are shipped, while persistent pre-review candidate inboxes
  remain future work under BTN-34.
- RFC-0004 requires every desktop client to remain disposable presentation:
  clients may not invoke LangGraph, mutate RunState, or create a second
  persistence authority. BTN-30 establishes that shared boundary, and BTN-31
  keeps worker metadata non-authoritative while allowing clients to reconnect
  after process separation.
