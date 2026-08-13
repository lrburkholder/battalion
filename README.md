# battalion

A LangGraph-based orchestrator that runs parts of the SDLC as a connected graph with explicit, human-controlled interrupt points, replacing manual slash-command handoff with handoff-to-orchestrator while preserving human oversight at defined decision points.

## Overview

Battalion is an AI-driven workflow orchestrator built on LangGraph that coordinates multiple specialized agents (nodes) to execute software development lifecycle tasks. Each node operates within strictly defined boundaries, with mechanical enforcement of write scopes and explicit interrupt points where human oversight is required.

The project follows a dogfooding approach: Battalion's first project is itself, with each component being built using the very patterns and constraints it will eventually enforce.

## Status

**Current Milestone**: v1 core complete; onboarding and observability next
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

## Architecture

### Core Components

| Component | Purpose | Status |
|-----------|---------|--------|
| `battalion.state.models` | Versioned state contract (Pydantic models) | ✅ Complete |
| `battalion.state.persistence` | Local JSON load/save | ✅ Complete |
| `battalion.scope.tool_binding` | Write-scope enforcement (ADR-002) | ✅ Complete |
| `battalion.llm.litellm_client` | Per-node model configuration | ✅ Complete |
| `battalion.nodes.architect` | Architecture planning node | ✅ Complete |
| `battalion.nodes.driver` | RED/GREEN implementation node (ADR-006) | ✅ Complete |
| `battalion.nodes.reviewer` | Skeptical review node, per-checkpoint rejection counters (ADR-007, ADR-009) | ✅ Complete |
| `battalion.nodes.refactorer` | Refactor node sharing Driver's write scope (ADR-008) | ✅ Complete |
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
- `run_id`: Unique run identifier
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
python -m pip install langgraph  # temporary until declared in pyproject.toml
```

Battalion requires Python 3.11 or newer. The project imports LangGraph at
runtime; until it is declared in `pyproject.toml`, install a compatible
`langgraph` package explicitly in a fresh environment.

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
python -m battalion resume run-BTN-16
```

Run `python -m battalion <command> --help` for the authoritative options while
the CLI is evolving.

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
├── cli.py                      # Typer commands: run/resume/status/setup
├── config.py                   # Configuration loading and validation
├── graph.py                    # StateGraph wiring, edges, interrupt points (BTN-7)
├── progress.py                 # CLI progress display
├── setup.py                    # Guided model/provider setup (BTN-15)
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
├── test_architect_node.py     # Architect node tests
├── test_driver_node.py        # Driver node tests
├── test_reviewer_node.py      # Reviewer node tests
├── test_refactorer_node.py    # Refactorer node tests
├── test_graph.py              # StateGraph wiring tests
├── test_interrupts.py         # Interrupt trigger tests
├── test_litellm_client.py     # LiteLLM client tests
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

## Dependencies

- **Python**: >= 3.11
- **Core**: 
  - `langgraph` - Graph construction and execution (runtime import; packaging declaration pending)
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
The CLI is deliberately the thinnest layer, wrapping already-working internals. This ensures the core functionality is testable and reusable without the CLI.

The reusable `run_ticket(initial_state, llm_configs, ...)` API takes one
complete, caller-created `RunState`. That state is the sole source of truth for
the run identifier, ticket identifier, specification, budget, manual
checkpoints, write scope, and retry bound. These values are not also accepted as
separate arguments, so callers cannot provide conflicting duplicate
configuration. `resume_ticket` likewise continues from the persisted
`RunState`, changing only the execution bookkeeping required to resume.

See the [complete ADR index](docs/adrs/README.md) for all accepted architecture
decisions and their implementation status.

## Contributing

1. **Fork and clone** the repository
2. **Create a branch** for your changes
3. **Add tests** for new functionality
4. **Run existing tests** to ensure nothing breaks
5. **Submit a pull request**

### Development Workflow

The project uses a ticket-based workflow where each significant feature or component has its own ticket in `backlog.json`. Tickets follow the BTN-# format and have explicit dependencies and acceptance criteria.

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

### Active Work

- ✅ Guided LLM configuration and connectivity validation (BTN-15)
- ✅ Role prompts aligned with node authority and output contracts (BTN-25)
- ✅ Persist and assemble role-appropriate node context (BTN-26)
- ⏳ Per-call and per-node cost reporting (BTN-16)
- ⏳ Interrupt/checkpoint web UI (BTN-17)

### Future Enhancements
- Researcher, Specifier, Teacher nodes (post-v1)
- Guardian node for severity-based review triggers
- Live JIRA/MCP ticket integration (plugin architecture)
- Battalion self-modifying its own graph/node definitions (future)

---

*Built with LangGraph, Pydantic, and LiteLLM*
*Dogfooding: Battalion's first project is itself*
