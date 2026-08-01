# Battalion v1 — Plan

## Architecture Overview
A LangGraph `StateGraph` with three nodes (Architect, Driver, Reviewer), Pydantic
models as the single state contract, LiteLLM as the model access layer, and
Typer as the CLI entry point. State persists to local JSON files matching the
`regiment-backlog.json` schema conventions. Each node is bound only to the file
write tools its declared scope permits — scope violation is structurally
impossible, not policy-checked.

```
battalion/
  cli.py                  # Typer entry point: run / resume / status
  graph.py                # StateGraph construction, edges, interrupt wiring
  state/
    models.py             # Pydantic models — the single versioned state contract
    persistence.py         # load/save to local JSON, schema_version handling
  nodes/
    architect.py
    driver.py
    reviewer.py
  scope/
    tool_binding.py        # per-node scoped write-tool factory
  interrupts/
    triggers.py            # the 5 v1 interrupt trigger checks
    budget.py               # per-graph-run budget tracking
  llm/
    litellm_client.py       # per-node model config wrapper
```

## ADR Log

### ADR-001: Pydantic for state validation
**Decision:** State schema is defined as Pydantic models, not JSON Schema +
dataclasses.
**Reasoning:** Validation and types live in one place; LangGraph state objects
pass naturally through nodes as Pydantic instances rather than needing a
separate validate-then-cast step. Cost: Pydantic becomes a hard dependency
across every node.

### ADR-002: Per-node tool binding for write-scope enforcement
**Decision:** Each node's LangGraph tool set is constructed at graph-build
time to only include write tools scoped to that node's declared paths. A node
is never *handed* a tool capable of writing outside its scope — enforcement is
structural, not a runtime permission check.
**Reasoning:** A central guard (checking every write against a scope table)
is weaker: it depends on every write path remembering to call the guard.
Per-node binding makes an out-of-scope write a `AttributeError`/missing-tool
failure, not a policy check that can be forgotten in a new code path.
**Consequence:** Interrupt trigger #2 (out-of-scope write attempt) becomes
largely a *defense-in-depth* check rather than the primary mechanism — worth
keeping in the taxonomy anyway, since a node could still attempt a call
that fails at the tool-binding layer, and that attempt is itself signal worth
logging/surfacing.

### ADR-003: Typer for CLI
**Decision:** Typer over Click or argparse.
**Reasoning:** Type-hint-driven, pairs with the Pydantic-heavy codebase,
minimal boilerplate for subcommands (`run`, `resume`, `status`).

### ADR-004: Full rewrite over wrapping existing Regi- agents
**Decision:** (Carried from findings.md) Architect/Driver/Reviewer are native
LangGraph node rewrites, not wrappers over Copilot prompts.
**Reasoning:** Explicit ownership transition off work assets; also means node
prompts can be designed for LiteLLM/Pydantic from the start rather than
retrofitted.

### ADR-005: Externalized per-node system prompts
**Decision:** Node system prompts live as plain text files in a top-level
`prompts/` directory (e.g. `prompts/architect.md`), not as Python string
constants in the node modules. Each node function accepts an optional
`system_prompt` override (mainly for tests) and `prompts_dir` override, but
defaults to loading its own file via `battalion.prompts.loader`.
**Reasoning:** Prompt content will be iterated on far more often than node
logic — swapping in a more detailed hand-tuned prompt (e.g. the existing
Squad Architect prompt) should be a file edit, not a code change or PR
touching `architect.py`. Applies to all nodes going forward (Driver,
Reviewer), and Architect (BTN-4) was retrofitted onto this pattern rather
than left on its original hardcoded constant.
**Consequence:** Node modules now depend on `battalion.prompts.loader`,
and each node needs a corresponding prompt file to exist before it can run
without an explicit override — missing/empty files raise `PromptNotFound`
rather than silently falling back to nothing.

## Sequencing (implementation order)
1. State models + persistence (nothing else can be built without this)
2. Per-node tool binding / write-scope mechanism
3. LiteLLM client wrapper
4. Architect node
5. Driver node
6. Reviewer node
7. Graph wiring (edges, node sequencing)
8. Interrupt triggers (1–5) + budget tracking
9. CLI (run / resume / status)
10. End-to-end acceptance criteria validation (per spec.md)

Rationale: 1–3 are shared infrastructure every node depends on, so they're
built once and built first. Nodes are built in their execution order (4–6)
since Driver's tests are easiest to write once Architect's output shape is
known, and same for Reviewer against Driver's output. Graph wiring and
interrupts come after the nodes exist to wire together. CLI last since it's
the thinnest layer, wrapping already-working internals.

## Risks / Watch Items
- Interrupt trigger #1 (same root cause twice) requires the Reviewer node to
  articulate rejection causes consistently enough to compare across cycles —
  this needs its own prompt-design attention, not just plumbing
- ADR-002's structural enforcement means a bug in tool-binding construction
  (not the node itself) could silently over- or under-scope a node — worth an
  explicit test per node confirming its exact tool set
- Budget-per-graph-run (not per-node) means a single runaway node could
  consume the whole ticket's budget before a slower, well-behaved node gets
  its share — acceptable for v1 but worth flagging as a known limitation

## Next Step
Break this plan into backlog tickets (`backlog.json`, following
`regiment-backlog.json` conventions) and hand off to `/Driver` per ticket.
