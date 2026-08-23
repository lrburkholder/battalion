# battalion

[![Test](https://github.com/lrburkholder/battalion/actions/workflows/test.yml/badge.svg)](https://github.com/lrburkholder/battalion/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://lrburkholder.github.io/battalion)

A LangGraph-based orchestrator that runs parts of the SDLC as a connected graph with explicit, human-controlled interrupt points, replacing manual slash-command handoff with handoff-to-orchestrator while preserving human oversight at defined decision points.

## Overview

Battalion is an AI-driven workflow orchestrator built on LangGraph that coordinates multiple specialized agents (nodes) to execute software development lifecycle tasks. Each node operates within strictly defined boundaries, with mechanical enforcement of write scopes and explicit interrupt points where human oversight is required.

The project follows a dogfooding approach: Battalion's first project is itself, with each component being built using the very patterns and constraints it will eventually enforce.

## Status

<!-- battalion:status:start -->

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

<!-- battalion:status:end -->

## Architecture

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

---

*Built with LangGraph, Pydantic, and LiteLLM*
*Dogfooding: Battalion's first project is itself*
