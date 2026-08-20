# battalion

A LangGraph-based orchestrator that runs parts of the SDLC as a connected graph with explicit, human-controlled interrupt points, replacing manual slash-command handoff with handoff-to-orchestrator while preserving human oversight at defined decision points.

## Overview

Battalion is an AI-driven workflow orchestrator built on LangGraph that coordinates multiple specialized agents (nodes) to execute software development lifecycle tasks. Each node operates within strictly defined boundaries, with mechanical enforcement of write scopes and explicit interrupt points where human oversight is required.

The project follows a dogfooding approach: Battalion's first project is itself, with each component being built using the very patterns and constraints it will eventually enforce.

## Status

**Current Milestone**: desktop operator UI and public product showcase

The v1 execution graph is complete. Durable execution evidence, the Recon and
Intel knowledge lifecycle, deterministic context assembly, and the desktop
operator architecture, shared application boundary, isolated active-run worker
supervision, durable run/project identity, operator evidence, and typed
live-observation are also implemented. After equivalent desktop spikes,
[ADR-0022](docs/adrs/adr0022.md) selects PySide6 with Qt Widgets. BTN-42's
production read-only console is implemented and validated on its feature branch;
state-changing desktop controls and the split runtime are implemented through
BTN-43. BTN-56 applies the accepted visual mockup and bundled brand assets.
BTN-57 adds a public Pages landing experience with reviewed production-client
screenshots and a concise, truthful introduction to the shipped workflow. The
feature is complete on its branch; the live site updates after merge to `main`.

See the [canonical Battalion backlog](backlog.json) for ticket scope,
dependencies, acceptance criteria, and current status.

- ✅ **BTN-1**: State models + persistence layer
- ✅ **BTN-2**: Per-node write-scope tool binding
- ✅ **BTN-3**: LiteLLM client wrapper
- ✅ **BTN-4**: Architect node
- ✅ **BTN-5**: Driver node
- ✅ **BTN-6**: Reviewer node
- ✅ **BTN-7**: Graph wiring with interrupt points
- ✅ **BTN-8**: Interrupt triggers (1-6) + budget tracking
- ✅ **BTN-9**: CLI (Typer) - run/resume/status
- ✅ **BTN-10**: End-to-end acceptance criteria validation
- ✅ **BTN-11**: Driver RED/GREEN mode support
- ✅ **BTN-12**: Reviewer expect_pass parameter + per-checkpoint rejection counters
- ✅ **BTN-13**: Refactorer node
- ✅ **BTN-14**: Model-diversity constraint (Reviewer must differ from Driver)
- ✅ **BTN-15**: CLI setup command for LLM configuration and validation
- ✅ **BTN-16**: Per-call LLM cost capture and per-phase reporting
- ❌ **BTN-17**: Narrow interrupt/checkpoint web UI (cancelled and superseded by [RFC-0004](docs/rfcs/rfc0004.md))
- ✅ **BTN-18**: Public GitHub Pages documentation
- ✅ **BTN-19**: Durable execution records and artifact provenance
- ✅ **BTN-20–23**: Instinct contracts, immutable persistence, Recon generation, and audited operator promotion
- ✅ **BTN-24**: Deterministic, role-specific Instinct retrieval and injection
- ✅ **BTN-25–28**: Role contracts, bounded context assembly, caller-owned run configuration, and layout-aware write scopes
- ✅ **BTN-29**: Desktop operator interface architecture ([RFC-0004](docs/rfcs/rfc0004.md))
- ✅ **BTN-30**: Shared application command and query boundary
- ✅ **BTN-31**: Active-run worker supervision
- ✅ **BTN-32**: Durable UUID run identity, display aliases, and project catalogs
- ✅ **BTN-33**: Operator summaries and revision evidence
- ✅ **BTN-34**: Immutable persisted Recon candidate inbox
- ✅ **BTN-35**: Exact, sourced, currency-aware usage evidence
- ✅ **BTN-36**: Typed live observation and durable-first reconnect contract
- ✅ **BTN-37–41**: Controlled desktop spikes and PySide6 framework selection
- ✅ **BTN-42**: Read-only PySide6 operator console
- ✅ **BTN-43**: Desktop interrupt, Recon review, and typed next-attempt actions
- ✅ **BTN-56**: Desktop visual system, IBM Plex typography, and brand assets
- ✅ **BTN-57**: GitHub Pages product showcase and production screenshots

## Architecture

### Core Components

| Component | Purpose | Status |
|-----------|---------|--------|
| `battalion.state.models` | Versioned state contract (Pydantic models) | ✅ Complete |
| `battalion.state.persistence` | Local JSON load/save | ✅ Complete |
| `battalion.intel.models` | Versioned candidate/accepted Instinct contract | ✅ Complete (BTN-20) |
| `battalion.intel.candidates` | Immutable Markdown Recon candidate inbox | ✅ Complete (BTN-34) |
| `battalion.intel.repository` | Immutable accepted-Instinct persistence | ✅ Complete (BTN-21) |
| `battalion.intel.review` | Audited operator review and promotion boundary | ✅ Complete (BTN-23) |
| `battalion.intel.retrieval` | Deterministic active-Instinct selection | ✅ Complete (BTN-24) |
| `battalion.application` | Typed run, resume, inspection, human-action, Intel-review, identity, and worker boundary shared by presentation clients | ✅ Complete (BTN-43) |
| `battalion.identity` | Canonical run UUIDs, project markers, legacy discovery, and project-local run catalogs | ✅ Complete (BTN-32) |
| `battalion.workers` | Detached per-run process supervision and durable reconnect evidence | ✅ Complete (BTN-31) |
| `battalion.observation` | Typed durable/transient live events, ordering, deduplication, and reconnect cursors | ✅ Complete (BTN-36) |
| `battalion.desktop` | PySide6 Work, History, execution evidence, Intel review, interrupt resolution, and next-attempt actions | ✅ Complete (BTN-42–43) |
| `battalion.execution` | Durable node execution, artifact provenance, and sourced usage evidence | ✅ Complete (BTN-16, BTN-19, BTN-35) |
| `battalion.context` | Bounded role context assembly and Instinct injection | ✅ Complete (BTN-26) |
| `battalion.scope.tool_binding` | Write-scope enforcement (ADR-002) | ✅ Complete |
| `battalion.llm.litellm_client` | Per-node model configuration | ✅ Complete |
| `battalion.nodes.architect` | Architecture planning node | ✅ Complete |
| `battalion.nodes.driver` | RED/GREEN implementation node (ADR-006) | ✅ Complete |
| `battalion.nodes.reviewer` | Skeptical review node, per-checkpoint rejection counters (ADR-007, ADR-009) | ✅ Complete |
| `battalion.nodes.refactorer` | Refactor node sharing Driver's write scope (ADR-008) | ✅ Complete |
| `battalion.nodes.recon` | Post-completion candidate Instinct generation | ✅ Complete (BTN-22) |
| `battalion.graph` | LangGraph StateGraph wiring, edges, interrupt pause points | ✅ Complete |
| `battalion.interrupts.triggers` | All 6 v1 interrupt trigger checks | ✅ Complete |
| `battalion.interrupts.budget` | Per-graph-run budget tracking (trigger #3) | ✅ Complete |
| `battalion.config` | YAML/environment/CLI configuration merge and model-diversity validation | ✅ Complete |
| `battalion.setup` | Provider discovery, configuration, and connectivity checks | ✅ Complete (BTN-15) |
| `battalion.progress` | Human-readable CLI progress events | ✅ Complete |
| `battalion.cli` | Typer CLI - run/resume/status/setup | ✅ Complete (BTN-9, BTN-15) |

### State Schema

The versioned state contract includes:
- `schema_version`: Schema version identifier
- `run_id`: UUID canonical identifier for new runs; legacy IDs remain readable
- `run_alias`: Optional human-readable display label
- `project_id`: Durable project UUID; optional on legacy state
- `ticket_id`: Current ticket being processed
- `spec`: Supplied ticket specification retained across pause and resume
- `status`: Current run status (not-started, in-progress, blocked, awaiting-human, done, failed-infra)
- `phase`: Current node/phase (architect, driver, reviewer, refactorer, pause, or done)
- `write_scope`: Per-node declared write permissions
- `reviewer_rejection_history`: Tracking for interrupt trigger #1
- `retry_bound`: Configurable retry limits
- `budget`: Per-graph-run budget tracking
- `interrupt_log`: History of all interrupt triggers
- `manual_checkpoints`: User-declared pause points
- `execution_record`: Durable node evidence, including per-call tokens and
  nullable decimal cost with separate currency and source

### Interrupt Taxonomy (v1)

| # | Trigger | Definition | Handling |
|---|---------|------------|----------|
| 1 | Reviewer rejects same root cause twice | Same root cause rejected twice on same ticket | Pause, escalate to human |
| 2 | Out-of-scope write attempt | Node tries to write outside declared scope | Hard block, mechanical check |
| 3 | Budget exceeded | Per-graph-run budget limit reached | Pause, show spend, ask to continue |
| 4 | Role-definition edit | Any modification to Battalion role definitions | Always interrupt |
| 5 | Infra failure | Node crash, malformed state, LiteLLM failure | Distinct failure state |
| 6 | Manual checkpoint | User-declared pause point | Graph pauses unconditionally |

### Write Scope Model

Each node declares which files/directories it may create/edit as part of its node definition. Scope is enforced mechanically through tool binding - nodes only receive tools bound to their declared paths, making out-of-scope writes structurally impossible.

New project layouts can give each writing phase its least-authority roots:

```yaml
write_scope:
  architect: ["plan.md"]
  driver_red: ["tests/"]
  driver_green: ["battalion/"]
  refactorer: ["battalion/"]
  reviewer: []
```

Reviewer runs test discovery from `base_dir`, the configured project root. A
legacy `driver: ["src/"]` declaration remains supported and is still the
default when no write scope is configured; RED, GREEN, and Refactorer all fall
back to it when their phase-specific entry is absent.

## Usage

### Installation

```bash
# Clone the repository
git clone https://github.com/lrburkholder/battalion.git
cd battalion

# Create a virtual environment, activate it for your shell, then install dependencies
python -m venv .venv
python -m pip install -e ".[dev]"

# Add the production desktop client when needed
python -m pip install -e ".[desktop,dev]"
```

Battalion requires Python 3.11 or newer. Core installation includes the
validated LangGraph 1.x runtime. The desktop extra adds pinned PySide6 and
Nuitka packaging tools.

### Configure Models

Battalion uses LiteLLM model identifiers such as `openai/gpt-4.1-mini` or
`anthropic/claude-sonnet-4-20250514`. Configure provider credentials in the
environment; do not put API keys in `battalion.config.yaml`.

BTN-15 adds a guided setup command:

```bash
python -m battalion setup

# Non-interactive example
python -m battalion setup \
  --model-architect provider/model-a \
  --model-driver provider/model-b \
  --model-reviewer provider/model-c \
  --model-refactorer provider/model-b
```

Driver and Reviewer must use different model identifiers. Use `--no-validate`
only when intentionally skipping live provider connectivity checks.

### Run Battalion

```bash
python -m battalion run BTN-16 --spec path/to/spec.md
python -m battalion status run-BTN-16 --human
python -m battalion status run-BTN-16 --costs --human
python -m battalion resume run-BTN-16
```

`status --costs` projects persisted LiteLLM input/output tokens and known cost
by concrete graph phase, currency, and source. Unknown monetary cost remains
explicit and never becomes zero; token usage is still shown. Without `--human`,
the command emits the cost summary as JSON. Cost reporting does not change the
run-level turn budget used by interrupt trigger #3.

Run `python -m battalion <command> --help` for the authoritative options while
the CLI is evolving.

### Browse with the Desktop Console

```bash
# Open the current project through the shared application boundary
battalion-desktop --project .

# Equivalent module entry point
python -m battalion.desktop --project .

# Fast UI-only package; graph and provider modules remain excluded
python scripts/build_desktop.py --component desktop

# Heavy detached-worker package; build independently when runtime code changes
python scripts/build_desktop.py --component worker

# Release build of both sibling distributions
python scripts/build_desktop.py --component all
```

The desktop client exposes Work, History, node-attempt evidence, accepted Intel,
and persisted Recon candidates. BTN-43 adds canonical interrupt resolution and
resume, candidate promotion/edit-promotion/rejection, Corrections for Driver RED,
Driver GREEN, or Refactorer, and Design decisions for Architect. Interventions
are queued only while no worker is active and are durably associated with the
target's next attempt before provider generation. Reviewer intervention,
Reviewer verdict override, and manual checkpoint override are absent. Refresh
and client restart reload authoritative local state; post-barrier live
observations are applied only after durable recovery.

BTN-43 packages the Qt client and execution worker separately. The client stays
small and locates `worker/worker_entry.dist/BattalionWorker.exe` beside its
distribution; source execution continues to use `python -m battalion.workers`.
Both builds exclude `pytest` through Nuitka's anti-bloat policy and emit XML
compilation reports under `dist/desktop/`. This makes ordinary UI packaging
independent of the much larger LangGraph/LiteLLM provider runtime.

The desktop visual system follows the design source in `ui/mockup/`: IBM Plex
Sans for interface text, IBM Plex Mono for operational evidence, charcoal
surfaces, compact two-pixel geometry, and a restrained blue accent. The OFL
font files and Battalion application icon are bundled in source distributions
and frozen desktop builds; no system font installation or network access is
required at runtime.

### Preview the Production UI

[![Battalion Work view showing an awaiting-human run and operator actions](docs/assets/screenshots/battalion-work.png)](https://lrburkholder.github.io/battalion/)

The public showcase uses the real PySide6 client with deterministic fictional
data. It does not read a developer's run state, provider configuration, or
credentials. The [screenshot refresh procedure](docs/ui/showcase.md) documents
how to reproduce and review the Work, History, and Intel captures. The
[operator workflow](docs/ui/workflow.md) remains the complete text path through
the same shipped functionality.

### Running Tests

```bash
# Run all tests (offline; provider calls are mocked)
python -m pytest

# Optional coverage report (requires pytest-cov)
python -m pip install pytest-cov
python -m pytest --cov=battalion --cov-report=term-missing
```

### Project Structure

```
battalion/
├── __init__.py
├── __main__.py                 # `python -m battalion`
├── application.py              # Shared typed command/query boundary (BTN-30)
├── desktop/                    # PySide6 operator console (BTN-42–43)
│   ├── app.py                  # Qt Widgets and desktop entry point
│   ├── controller.py           # Background application queries and reconnect
│   └── presentation.py         # Pure evidence and missing-data projections
├── workers.py                  # Per-run worker supervision and recovery (BTN-31)
├── worker_entry.py             # Split frozen worker entry point (BTN-43)
├── observation.py              # Typed live observation contract (BTN-36)
├── cli.py                      # Thin Typer presentation adapter
├── config.py                   # Configuration loading and validation
├── context.py                  # Bounded node context assembly (BTN-26)
├── execution.py                # Durable execution evidence and cost projection
├── graph.py                    # StateGraph wiring, edges, interrupt points (BTN-7)
├── progress.py                 # CLI progress display
├── setup.py                    # Guided model/provider setup (BTN-15)
├── intel/
│   ├── candidates.py           # Create-only Recon Markdown inbox (BTN-34)
│   ├── models.py               # Candidate and accepted Instinct contracts (BTN-20)
│   ├── repository.py           # Immutable accepted-Instinct storage (BTN-21)
│   ├── retrieval.py            # Deterministic Instinct selection (BTN-24)
│   └── review.py               # Operator decisions and promotion workflow (BTN-23)
├── llm/
│   ├── __init__.py
│   └── litellm_client.py      # Per-node LiteLLM wrapper (BTN-3)
├── interrupts/
│   ├── __init__.py
│   ├── triggers.py            # All 6 v1 interrupt trigger checks (BTN-8)
│   └── budget.py              # Per-graph-run budget tracking (BTN-8)
├── nodes/
│   ├── __init__.py
│   ├── architect.py           # Architect node (BTN-4)
│   ├── driver.py               # Driver node, RED/GREEN modes (BTN-5, BTN-11)
│   ├── reviewer.py             # Reviewer node, expect_pass + per-checkpoint counters (BTN-6, BTN-12)
│   ├── refactorer.py           # Refactorer node (BTN-13)
│   ├── recon.py                # Post-completion candidate generation (BTN-22)
│   └── errors.py               # Shared node error types
├── scope/
│   ├── __init__.py
│   └── tool_binding.py        # Write-scope tool binding (BTN-2)
└── state/
    ├── __init__.py
    ├── models.py              # State models (BTN-1)
    └── persistence.py          # JSON persistence (BTN-1)

prompts/                        # Node system prompts, overridable per node
├── architect.md
├── driver.md
├── driver-red.md
├── driver-green.md
├── reviewer.md
└── refactorer.md

tests/
├── test_acceptance.py         # End-to-end v1 acceptance criteria
├── test_application.py        # Shared application-boundary tests (BTN-30)
├── test_architect_node.py     # Architect node tests
├── test_driver_node.py        # Driver node tests
├── test_reviewer_node.py      # Reviewer node tests
├── test_refactorer_node.py    # Refactorer node tests
├── test_graph.py              # StateGraph wiring tests
├── test_interrupts.py         # Interrupt trigger tests
├── test_litellm_client.py     # LiteLLM client tests
├── test_instinct_review.py    # Operator review and promotion tests
├── test_models.py            # State model tests
├── test_persistence.py        # Persistence tests
├── test_prompt_loader.py      # Prompt loading/override tests
├── test_setup.py              # Guided setup tests (BTN-15)
└── test_tool_binding.py       # Tool binding tests

# Configuration
├── pyproject.toml            # Project metadata and dependencies
├── backlog.json              # Project backlog and ticket tracking
└── spec.md                   # Detailed specification and ADRs
```

Recon candidate evidence is stored at
`<project>/.battalion/recon/candidates/INS-....md`. YAML front matter is the
validated machine contract and the remaining Markdown is its deterministic
operator-readable rendering. Candidate files are create-only. Promotion or
rejection is represented by a separate append-only review decision under
`<project>/.battalion/recon/decisions/`, so the original candidate remains
unchanged. Rejected candidates are retained
indefinitely for audit evidence; the persistence API intentionally exposes no
delete operation.

## Dependencies

- **Python**: >= 3.11
- **Core**: 
  - `langgraph>=1.2,<2.0` - Graph construction and execution
  - `pydantic>=2.0` - Data validation and models
  - `litellm>=1.40` - Multi-provider LLM abstraction
  - `typer>=0.9` - CLI framework
  - `pyyaml>=6.0` - YAML configuration
- **Development**:
  - `pytest>=8.0` - Testing framework
  - `pytest-cov` - Coverage reporting

## Design Principles

### [ADR-0001: Single Versioned State Schema](docs/adrs/adr0001.md)
All nodes share a single, versioned state contract rather than maintaining separate schemas. This ensures consistency across the graph and simplifies state management.

### [ADR-0002: Structural Write Scope Enforcement](docs/adrs/adr0002.md)
Nodes only receive tools bound to their declared write paths. This provides defense-in-depth: out-of-scope writes are prevented structurally (missing tool) rather than via runtime permission checks.

### [ADR-0003: CLI Design](docs/adrs/adr0003.md)
The CLI is deliberately a presentation adapter over transport-neutral
application commands and queries. This keeps graph policy, persistence, and
human-authorized operations reusable by future graphical clients without
creating parallel run or resume implementations.

The BTN-30 application boundary accepts one complete, caller-created
`RunState`, returns typed results and documented domain failures, and owns calls
to the canonical graph and persistence functions. The state remains the sole
source of truth for run configuration. Clients cannot provide conflicting
duplicate configuration or mutate persisted state directly.

BTN-31 extends that boundary with start, observe, cancel, and reconnect worker
operations. Each active run executes in a detached Python process associated
with one canonical run ID. Project-local worker metadata reports lifecycle and
crash recovery, while atomically saved `RunState` remains the execution
authority; reconnecting clients never need the original process handle.

ADR-0023 keeps human actions with their existing authority. Interrupt
resolutions and typed interventions are durable `RunState` evidence; candidate
review remains in the append-only Intel decision repository. Both CLI and
desktop resume through the same application command and graph path. A delivered
intervention is tied to one node-attempt ID, included through a named bounded
context section, and recorded in execution context provenance.

See the [complete ADR index](docs/adrs/README.md) for all accepted architecture
decisions and their implementation status.

## Contributing

1. **Fork and clone** the repository
2. **Create a branch** for your changes
3. **Add tests** for new functionality
4. **Run existing tests** to ensure nothing breaks
5. **Submit a pull request**

### Development Workflow

The project uses a ticket-based workflow where each significant feature or
component has a ticket in the [canonical `backlog.json`](backlog.json). Tickets
follow the BTN-# format and have explicit dependencies, acceptance criteria,
and lifecycle status.

GitHub Issues are not the source of truth yet. The repository backlog is
machine-readable, works offline and across forges, and lets Battalion validate
ticket identity and dependencies without relying on an external service. Moving
or mirroring tickets into GitHub Issues needs an explicit synchronization and
ownership policy first; otherwise the two trackers could silently disagree.

## License

MIT License - Copyright (c) 2026 Luke Burkholder

See [LICENSE](LICENSE) for full license text.

## Roadmap

### v1 Milestone (Complete)
- ✅ Driver, Reviewer, and Refactorer nodes complete
- ✅ Full graph wiring with LangGraph (RED → Reviewer → GREEN → Reviewer → Refactorer → Reviewer loop)
- ✅ All 6 interrupt trigger implementations + budget tracking
- ✅ Build CLI entry points (BTN-9)
- ✅ End-to-end acceptance testing (BTN-10)
- ✅ Model-diversity constraint between Driver and Reviewer (BTN-14)
- ✅ [Public documentation site](https://lrburkholder.github.io/battalion/) (BTN-18)

### v2 Milestone — Desktop Operator UI

v2 is the desktop user-interface release. Its goal is to make Battalion's
existing human-directed workflow observable and operable without moving graph,
persistence, scope, or interrupt authority into the presentation client.

- **UI foundations (BTN-29–36, complete):** accepted operator architecture,
  shared application commands, isolated workers, durable run/project identity,
  operator evidence, candidate persistence, usage semantics, and a live
  observation contract are all merged.
- **Framework selection (BTN-37–41, complete):** the shared fixture and
  [measurement procedure](benchmarks/desktop/README.md) drove equivalent Tauri,
  PySide6, and Electron release prototypes. [ADR-0022](docs/adrs/adr0022.md)
  selects PySide6 with Qt Widgets and carries its packaging, accessibility,
  recovery, and ambient-authority risks into production acceptance.
- **Production UI:** BTN-42 implements the read-only operator console. BTN-43
  adds human-action surfaces, and BTN-44 adds history search and descriptive
  model-by-role analytics.

The production PySide6 client is separate from the disposable benchmark code
and depends on `battalion.application` rather than LangGraph or persistence.

### Future Architecture Planning

These directions now have bounded architecture tickets and draft planning
briefs under `docs/future/`. They are not implementation commitments, and none
changes Battalion's current roles or authority until its RFC is accepted:

- **BTN-45:** Decide whether and where a human-reviewed Specifier role belongs.
- **BTN-46:** Specify a permissioned plugin architecture and integration
  lifecycle without creating a second policy or persistence authority.
- **BTN-47:** Decide whether severity-based review needs a Guardian role or a
  narrower extension of existing Reviewer and interrupt policy.
- **BTN-48:** Define safety invariants and staged evidence for bounded,
  human-authorized self-modification proposals.
- **BTN-49:** Decide where deterministic repository quality gates belong and
  evaluate a language-neutral contract using anti-slop and Python fixtures.
- **BTN-50:** Add a restrained README badge set backed by real CI,
  documentation, license, and repository state.
- **BTN-51–55:** Define transparent local and zero-cost inference policy, then
  add endpoint-aware setup, optional FreeLLMAPI support, resolved inference
  provenance, and mechanical cost-policy enforcement.

The former Teacher concept is no longer planned as a Battalion role. It is
expected to evolve into **Dojo**, a separate future application. Researcher
workflows may likewise belong in a separate application; that product boundary
is still unresolved and is not part of Battalion's current roadmap.

---

*Built with LangGraph, Pydantic, and LiteLLM*
*Dogfooding: Battalion's first project is itself*
