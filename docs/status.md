Battalion status at a glance. `backlog.json` is the machine-readable source of
truth for ticket identity, scope, dependencies, and status; this
page is its human-readable projection and is embedded verbatim into
`README.md`. Regenerate after backlog changes with
`python scripts/sync_status.py`.

## Current milestone

**Current Milestone**: desktop operator UI and public product showcase.

The v1 execution graph is complete. Durable execution evidence, the Recon and
Intel knowledge lifecycle, deterministic context assembly, the desktop
operator architecture, shared application boundary, isolated active-run worker
supervision, durable run/project identity, durable Actor identity and local
bootstrap, operator evidence, typed live-observation, and the PySide6 console
with human-action surfaces (BTN-42–43, BTN-56, BTN-59) are implemented.
BTN-50 repository badges are in flight. BTN-57 adds the public Pages landing
experience with reviewed production-client screenshots; it is complete on its
feature branch, and the live site updates after merge to `main`.

## Delivered work

<!-- BEGIN GENERATED:backlog-delivery (regenerate with: python scripts/sync_status.py) -->

### Shipped

| Ticket | Title | Status |
| --- | --- | --- |
| BTN-1 | State models + persistence layer | Yes |
| BTN-2 | Per-node write-scope tool binding | Yes |
| BTN-3 | LiteLLM client wrapper | Yes |
| BTN-4 | Architect node | Yes |
| BTN-5 | Driver node | Yes |
| BTN-6 | Reviewer node | Yes |
| BTN-7 | Graph wiring — StateGraph, edges, interrupt points | Yes |
| BTN-8 | Interrupt triggers (1-6) + budget tracking | Yes |
| BTN-9 | CLI (Typer) — run / resume / status | Yes |
| BTN-10 | End-to-end acceptance criteria validation | Yes |
| BTN-11 | Driver RED/GREEN mode support | Yes |
| BTN-12 | Reviewer expect_pass parameter + per-checkpoint rejection counters | Yes |
| BTN-13 | Refactorer node | Yes |
| BTN-14 | Model-diversity constraint: Reviewer must differ from Driver | Yes |
| BTN-15 | CLI setup command for LLM configuration and validation | Yes |
| BTN-16 | Cost/budget reporting granularity | Yes |
| BTN-18 | Public GitHub Pages site | Yes |
| BTN-19 | Durable execution record and artifact provenance | Yes |
| BTN-20 | Instinct data contract | Yes |
| BTN-21 | Immutable Intel repository | Yes |
| BTN-22 | Recon candidate generation | Yes |
| BTN-23 | Operator review and Instinct promotion workflow | Yes |
| BTN-24 | Deterministic retrieval and node-specific Instinct injection | Yes |
| BTN-25 | Align role prompts with node contracts | Yes |
| BTN-26 | Persist and assemble node execution context | Yes |
| BTN-27 | Preserve caller-supplied run configuration | Yes |
| BTN-28 | Project-layout-aware scoped writes | Yes |
| BTN-29 | Desktop operator interface architecture RFC | Yes |
| BTN-30 | Application command and query boundary | Yes |
| BTN-31 | Active-run worker supervision | Yes |
| BTN-32 | Run and project identity | Yes |
| BTN-33 | Operator summaries and revision evidence | Yes |
| BTN-34 | Recon candidate persistence | Yes |
| BTN-35 | Usage evidence revision | Yes |
| BTN-36 | Live observation contract | Yes |
| BTN-37 | Desktop framework benchmark fixture | Yes |
| BTN-38 | Tauri desktop architecture spike | Yes |
| BTN-39 | PySide6 desktop architecture spike | Yes |
| BTN-40 | Electron desktop architecture spike | Yes |
| BTN-41 | Desktop framework selection ADR | Yes |
| BTN-42 | Read-only desktop operator console | Yes |
| BTN-43 | Desktop human-action surfaces | Yes |
| BTN-51 | Zero-cost inference and model-endpoint architecture RFC | Yes |
| BTN-56 | Desktop visual system and brand assets | Yes |
| BTN-57 | GitHub Pages product showcase and visual assets | Yes |
| BTN-58 | Human identity, authority, assignment, and collaboration RFC | Yes |
| BTN-59 | Durable Actor identity and local operator provenance | Yes |
| BTN-65 | Transport-neutral integration capability architecture | Yes |

### In flight

| Ticket | Title | Status |
| --- | --- | --- |
| BTN-50 | Repository status badges and README trust signals | Wip |

### Cancelled

| Ticket | Title | Status |
| --- | --- | --- |
| BTN-17 | Interrupt/checkpoint web UI | No |

Ticket scope, dependencies, acceptance criteria, and the 31 planned (not-started) tickets live in the canonical [backlog.json](backlog.json).

<!-- END GENERATED:backlog-delivery -->

## Component readiness

| Component | Purpose | Status |
|-----------|---------|--------|
| `battalion.state.models` | Versioned state contract (Pydantic models) | Complete |
| `battalion.state.persistence` | Local JSON load/save | Complete |
| `battalion.intel.models` | Versioned candidate/accepted Instinct contract | Complete (BTN-20) |
| `battalion.intel.candidates` | Immutable Markdown Recon candidate inbox | Complete (BTN-34) |
| `battalion.intel.repository` | Immutable accepted-Instinct persistence | Complete (BTN-21) |
| `battalion.intel.review` | Audited operator review and promotion boundary | Complete (BTN-23) |
| `battalion.intel.retrieval` | Deterministic active-Instinct selection | Complete (BTN-24) |
| `battalion.application` | Typed run, resume, inspection, human-action, Intel-review, identity, and worker boundary shared by presentation clients | Complete (BTN-43) |
| `battalion.actors` | Durable human/system Actor identity, offline FTUE bootstrap, selection, and project-local persistence | Complete (BTN-59) |
| `battalion.identity` | Canonical run UUIDs, project markers, legacy discovery, and project-local run catalogs | Complete (BTN-32) |
| `battalion.workers` | Detached per-run process supervision and durable reconnect evidence | Complete (BTN-31) |
| `battalion.observation` | Typed durable/transient live events, ordering, deduplication, and reconnect cursors | Complete (BTN-36) |
| `battalion.desktop` | PySide6 Work, History, execution evidence, Intel review, interrupt resolution, and next-attempt actions | Complete (BTN-42–43) |
| `battalion.execution` | Durable node execution, artifact provenance, and sourced usage evidence | Complete (BTN-16, BTN-19, BTN-35) |
| `battalion.context` | Bounded role context assembly and Instinct injection | Complete (BTN-26) |
| `battalion.scope.tool_binding` | Write-scope enforcement (ADR-002) | Complete |
| `battalion.llm.litellm_client` | Per-node model configuration | Complete |
| `battalion.nodes.architect` | Architecture planning node | Complete |
| `battalion.nodes.driver` | RED/GREEN implementation node (ADR-006) | Complete |
| `battalion.nodes.reviewer` | Skeptical review node, per-checkpoint rejection counters (ADR-007, ADR-009) | Complete |
| `battalion.nodes.refactorer` | Refactor node sharing Driver's write scope (ADR-008) | Complete |
| `battalion.nodes.recon` | Post-completion candidate Instinct generation | Complete (BTN-22) |
| `battalion.graph` | LangGraph StateGraph wiring, edges, interrupt pause points | Complete |
| `battalion.interrupts.triggers` | All 6 v1 interrupt trigger checks | Complete |
| `battalion.interrupts.budget` | Per-graph-run budget tracking (trigger #3) | Complete |
| `battalion.config` | YAML/environment/CLI configuration merge and model-diversity validation | Complete |
| `battalion.setup` | Provider discovery, configuration, and connectivity checks | Complete (BTN-15) |
| `battalion.progress` | Human-readable CLI progress events | Complete |
| `battalion.cli` | Typer CLI - run/resume/status/setup | Complete (BTN-9, BTN-15) |

## Roadmap

The v1 milestone (graph, interrupts, CLI, acceptance testing, documentation)
is complete. Remaining work, by theme:

- **Desktop v2:** history search and model-by-role analytics remain
  (BTN-44).
- **Accepted architecture awaiting runtime delivery:** endpoint-aware
  inference identity and zero-cost policy (BTN-52–55, per RFC-0005 /
  ADR-0024); Actor capability enforcement, assignment/ownership, and
  authentication (BTN-60–62, per RFC-0007 / ADR-0026); transport-neutral
  integration capabilities (BTN-66–80, per RFC-0006 / ADR-0025), including
  the WorkSource abstraction (BTN-71) and GitHub Issues adapter (BTN-72)
  that would let GitHub Issues become a Battalion ticket source.
- **Future direction RFCs:** Specifier role (BTN-45), plugin architecture
  (BTN-46), severity-based review and a possible Guardian role (BTN-47),
  bounded self-modification safety (BTN-48), and pluggable repository quality
  gates (BTN-49). Draft briefs live under `docs/future/`; none changes
  Battalion's roles or authority until its RFC is accepted.

The former Teacher concept is no longer planned as a Battalion role. It is
expected to evolve into **Dojo**, a separate future application. Researcher
workflows may likewise belong in a separate application; that product boundary
is still unresolved and is not part of Battalion's current roadmap.
