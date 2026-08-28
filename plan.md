# Battalion v1 — Architecture Plan

## Status

The v1 execution architecture is complete and validated. BTN-16 and BTN-19
through BTN-28 add durable cost and execution evidence, the human-audited Recon
and Intel lifecycle, deterministic context assembly, caller-owned run
configuration, and project-layout-aware scope enforcement. RFC-0004 and all
desktop foundations through BTN-36 are merged: the shared application boundary,
detached worker supervision, canonical run/project identity, bounded operator
and revision evidence, immutable Recon candidate persistence, exact sourced
usage evidence, and durable-first live observation. BTN-37 through BTN-41 add
the shared provider-free benchmark, three disposable framework spikes, and the
accepted PySide6 desktop presentation decision in ADR-0022. BTN-42 implements
the production read-only desktop console. BTN-43 completes the accepted durable
human-action and next-attempt intervention contract in ADR-0023, including split
desktop/worker packaging; analytics remain BTN-44 work.
BTN-56 applies the supplied desktop visual tokens and bundled brand assets
without changing application authority or workflow behavior.
BTN-57 is complete on its feature branch: it adds a public Pages product
introduction, reviewed production-client captures from deterministic
credential-free demo projections, and an explicit publication and visual-QA
path. The live Pages site updates only after merge to `main`. The fixture is
presentation data only and does not change the application boundary, role
authority, graph, interrupt semantics, or knowledge lifecycle.
BTN-65 is complete with accepted RFC-0006 and ADR-0025. They separate Battalion
capability contracts from provider adapters and transports and define the six
initial capability boundaries. BTN-67 now supplies the validated,
least-authority capability-to-adapter-to-transport runtime; individual
capability operations remain follow-up implementation work.
BTN-58 is complete on its feature branch with accepted
[RFC-0007](docs/rfcs/rfc0007.md) and
[ADR-0026](docs/adrs/adr0026.md). They define durable Actor identity, explicit
project capabilities, FTUE bootstrap provenance, assignment and Ticket
ownership history, and non-authoritative collaboration evidence. Runtime
BTN-59 now implements the durable project-local Actor registry, offline FTUE
bootstrap evidence, local human selection, application queries, and compatible
Actor attribution for run and Recon evidence. BTN-63 is in progress to add
credential-free, integration-scoped external identity mappings that resolve to
Actors without granting authority. Capability enforcement, assignment/ownership,
and authentication remain BTN-60 through BTN-62.

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
  actors.py               # durable Actor contract, bootstrap, and local registry
  identity.py             # canonical UUIDs, project markers, and run catalogs
  workers.py              # detached per-run process supervision and recovery
  observation.py          # typed live events, ordering, and reconnect cursors
  cli.py                  # Typer adapter: run, resume, status, setup
  config.py               # YAML, environment, and CLI configuration merge
  context.py              # bounded role, Instinct, and human-action assembly
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
benchmarks/desktop/        # shared disposable framework-spike control case
tests/                    # unit and end-to-end acceptance tests
```

Dependencies point toward application policy. The CLI delegates run, resume,
inspection, costs, and persistence to `battalion.application`; future graphical
clients must use the same boundary. Filesystem, network, LiteLLM, and LangGraph
wiring remain boundary concerns, while role intent and state invariants do not
depend on a presentation transport.

## ADR log

The canonical decision records live in [`docs/adrs/`](docs/adrs/README.md).
The architecture decisions and active proposals referenced by this plan are:

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
| [ADR-0021](docs/adrs/adr0021.md) | Recover live observation from durable state |
| [ADR-0022](docs/adrs/adr0022.md) | Use PySide6 for desktop presentation |
| [ADR-0023](docs/adrs/adr0023.md) | Persist human actions with their existing authority |
| [ADR-0024](docs/adrs/adr0024.md) | Keep inference identity and cost policy in Battalion |
| [ADR-0025](docs/adrs/adr0025.md) | Put provider adapters and transports beneath Battalion capabilities |
| [ADR-0026](docs/adrs/adr0026.md) | Separate Actor identity, authority, and responsibility |
| [ADR-0027](docs/adrs/adr0027.md) | Generate status documentation from canonical GitHub Issues and Milestones |
| [ADR-0031](docs/adrs/adr0031.md) | Separate canonical status validation from public status rendering |
| [ADR-0028](docs/adrs/adr0028.md) | Authorize Battalion operations, not identities or transports |
| [ADR-0029](docs/adrs/adr0029.md) | Persist side-effect evidence in RunState with replay-safe logical operation identity |

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
17. Typed live observation and durable-first reconnect semantics (BTN-36).
18. Exact decimal, currency-aware, explicitly sourced usage evidence (BTN-35).
19. Bounded operator summaries and revision evidence (BTN-33).
20. Immutable Recon candidate persistence and decision-backed inbox (BTN-34).

BTN-37 defines the shared desktop fixture, scenario, acceptance validator, and
measurement procedure used unchanged by the three framework spikes.

BTN-38 through BTN-40 retain equivalent Tauri, PySide6, and Electron evidence.
ADR-0022 completes BTN-41 by selecting PySide6 with Qt Widgets for production
presentation while carrying its packaging, native accessibility, recovery, and
ambient-authority limitations into BTN-42 acceptance.

BTN-42 adds the production `battalion.desktop` presentation, pure evidence
projections, background project and Intel queries, durable-first live recovery,
explicit missing/failure states, a standalone deployment path, and native
accessibility evidence. Package exports and graph execution remain lazy so the
read-only client does not initialize graph or provider authority.

BTN-43 follows accepted ADR-0023. It adds durable interrupt-resolution and
typed intervention operations, exact next-attempt delivery with pre-generation
checkpointing and provenance, canonical Recon review commands, and accessible
PySide6 action controls. Reviewer intervention, verdict override, and manual
checkpoint override remain outside the product contract. Release packaging
keeps the Qt presentation and graph/provider worker in sibling standalone
distributions, allowing UI-only builds to retain the BTN-42 graph and LiteLLM
exclusions. Both components exclude pytest and emit Nuitka compilation reports;
the worker's heavier dependency closure can be built independently.

Later backlog work must build on these contracts instead of introducing parallel
run, resume, persistence, or review paths.

## Future architecture planning

BTN-45 through BTN-49 turn the README's post-v2 directions into bounded
architecture work for a possible Specifier role, a permissioned plugin model,
severity-based review and a possible Guardian role, and carefully constrained
self-modification research. BTN-49 adds a follow-on decision and disposable
evaluation for language-neutral repository quality gates after BTN-46 defines
the plugin boundary. The existing planning briefs live under `docs/future/`.

These tickets produce decision-ready RFCs and follow-up decomposition only.
They do not authorize new roles, graph transitions, interrupt behavior,
permissions, integrations, or self-editing. Any accepted change to those
surfaces must reconcile `spec.md`, record durable decisions in ADRs, preserve
human approval boundaries, and receive separate implementation tickets.

BTN-50 is independent rollout work for truthful repository status badges and a
credential-independent test workflow. It must not advertise packages,
services, contribution policy, or repository health that Battalion cannot
verify.

BTN-57 is independent Pages presentation work built on BTN-18, BTN-42, BTN-43,
and BTN-56. It adds canonical icons and deterministic, credential-free
screenshots of shipped desktop workflows. It coordinates layout with BTN-50 but
does not absorb badge or repository-health claims.

BTN-51 through BTN-55 define an architecture-first inference-target and cost
policy, then sequence endpoint-aware local setup, optional FreeLLMAPI support,
resolved identity evidence, and zero-cost enforcement. FreeLLMAPI remains a
replaceable OpenAI-compatible infrastructure option: its routing must not own
Battalion's role, diversity, cost, graph, or failure policy, and an external
free-tier claim is not itself durable zero-cost evidence.

BTN-51 accepted [RFC-0005](docs/rfcs/rfc0005.md) and
[ADR-0024](docs/adrs/adr0024.md). They separate requested and resolved identity,
endpoint and inference location, canonical model family, and cost policy. They
require fail-closed local-only and free-only modes while retaining LiteLLM and
BTN-35 unknown-cost semantics. Runtime delivery remains BTN-52 through BTN-55.

BTN-65's accepted [ADR-0025](docs/adrs/adr0025.md) places transport-neutral
Battalion capabilities above provider adapters and transports. Accepted
[RFC-0006](docs/rfcs/rfc0006.md) defines WorkSource, KnowledgeSource,
RepositoryService, Notification, OutboundEventSink, and HumanInteraction above
interchangeable native/local, HTTP/REST, webhook, MCP, or protocol-specific
transports. MCP and future plugins are optional implementation mechanisms, not
competing policy or graph authorities. BTN-66 now provides portable,
credential-free project integration configuration with stable IDs, provider /
transport / capability declarations, symbolic secret references, and bounded
organization/Actor precedence. BTN-67 now resolves those bindings through
registered adapters and bounded transports with deterministic typed failures.
BTN-70 now makes externally visible operations replay-safe: a versioned
side-effect ledger inside `RunState` records write-ahead intent, typed
attempt outcomes, and reconciliation evidence under Battalion-minted stable
logical operation IDs (ADR-0029). Operation policy and health validation
remain BTN-68 and BTN-69. BTN-73 now adds versioned, minimized outbound
machine-event envelopes after durable Run transitions. BTN-74 is in progress
adding a generic, vendor-neutral HTTP webhook OutboundEventSink: configured
selected event types post through one bounded endpoint with symbolic
authorization, a stable idempotency identity, and BTN-70 outcome semantics.
BTN-72 is in progress delivering the GitHub Issues
WorkSource adapter: repository-bound Issue normalization remains above
replaceable transports, and any accepted GitHub mutation must pass through
application policy plus the shared ledger rather than granting graph nodes
GitHub access. The remaining capability operations continue to consume the
ledger rather than redefining delivery semantics.

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
  Refactorer through one canonical context path. BTN-129 narrows Refactorer
  writes to the latest successful GREEN Driver artifacts when provenance is
  available.
- BTN-24 selects active accepted Instincts with literal audience,
  applicability, and tag rules, then injects whole identified entries through
  that same bounded context path for every execution role.
- Recon is the canonical name for Battalion's knowledge-capture role; Learner
  refers only to its historical Regiment predecessor. Candidate generation,
  create-only Markdown persistence, deterministic inbox discovery, and audited
  operator promotion are shipped without granting Recon publication authority.
- RFC-0004 requires every desktop client to remain disposable presentation:
  clients may not invoke LangGraph, mutate RunState, or create a second
  persistence authority. BTN-30 establishes that shared boundary, and BTN-31
  keeps worker metadata non-authoritative while allowing clients to reconnect
  after process separation.
- BTN-36 classifies live events as durable-backed facts, lossy progress, or
  action requests. Per-run operation sequences support concurrent workers;
  reconnect always reloads `RunState` before consuming post-barrier events.
- ADR-0022 selects PySide6 for desktop presentation. BTN-42 keeps widgets as
  thin adapters over `battalion.application` and validates accessibility,
  packaging, restart, and real-worker failure behavior without granting graph
  or persistence authority.
