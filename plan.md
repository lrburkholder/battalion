# Battalion v1 — Plan

## Architecture Overview
A LangGraph `StateGraph` with four nodes (Architect, Driver, Reviewer,
Refactorer — Refactorer added during the architecture pass, see ADR-008),
Pydantic models as the single state contract, LiteLLM as the model access
layer, and Typer as the CLI entry point. State persists to local JSON files
matching the `regiment-backlog.json` schema conventions. Each node is bound
only to the file write tools its declared scope permits — scope violation is
structurally impossible, not policy-checked.

```
battalion/
  cli.py                  # Typer entry point: run / resume / status
  graph.py                # StateGraph construction, edges, interrupt wiring
  state/
    models.py             # Pydantic models — the single versioned state contract
    persistence.py         # load/save to local JSON, schema_version handling
  nodes/
    architect.py
    driver.py               # gains mode: Literal["red", "green"] (BTN-11)
    reviewer.py              # gains expect_pass: bool (BTN-12)
    refactorer.py            # new (BTN-13) — shares driver's write_scope entry
    errors.py
  scope/
    tool_binding.py        # per-node scoped write-tool factory
  interrupts/
    triggers.py            # the 6 v1 interrupt trigger checks
    budget.py               # per-graph-run budget tracking
  llm/
    litellm_client.py       # per-node model config wrapper
```
*(See "Module Layout (updated)" below for the post-architecture-pass diff —
this block reflects the current shipped shape directly rather than as a
diff against the original 3-node draft.)*

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

### ADR-006: Driver gets a mode parameter (RED / GREEN)
**Decision:** `run_driver` takes a `mode: Literal["red", "green"]` parameter.
RED mode's prompt/output contract asks only for failing test files; GREEN
mode's asks only for implementation files against tests that already
exist. Both still return the same `{"files": {...}}` shape — mode changes
what the LLM is asked to produce, not the extraction/write mechanics.
**Reasoning:** Splits Driver's original single "write tests + implementation
together" call (BTN-5) into two checkpointed steps, so Reviewer can verify
red-then-green independently instead of trusting one combined LLM call to
get both right at once.
**Consequence:** BTN-5's original combined-call behavior needs a follow-on
ticket (BTN-11) to add mode support — this isn't a breaking rewrite of
BTN-5, mode is a new parameter, existing callers/tests are unaffected
until something actually passes a mode.

### ADR-007: Reviewer gets an expected-outcome parameter
**Decision:** `run_reviewer` takes an `expect_pass: bool` parameter.
Accept = tests match the expected outcome (fail, for RED-checkpoint; pass,
for GREEN/REFACTOR-checkpoints), not just "tests passed."
**Reasoning:** Caught during this architecture pass: BTN-6 as originally
built always treats passing as accept, which is backwards for the RED
checkpoint (a correctly-written failing test should be accepted, not
rejected). Without this, wiring BTN-7 straight onto BTN-6 would have
silently broken the RED checkpoint.
**Consequence:** Follow-on ticket (BTN-12) needed; also touches the state
schema (see ADR-009).

### ADR-008: Refactorer node, sharing Driver's write-scope entry
**Decision:** New Refactorer node, same shape as Driver (builds its own
scoped write tools internally, per the pattern established in BTN-4).
Refactorer builds its tools using the `"driver"` key in `write_scope`, not
a separate `"refactorer"` key — it shares the identical `src/` scope Driver
has, rather than write_scope carrying two entries with duplicate content
that could drift out of sync if the path ever changed.
**Reasoning:** Refactorer and Driver touch the same files for related
reasons (implementation code); a single shared scope declaration is the
source of truth for "who can write src/," rather than needing to keep two
entries consistent by hand.
**Consequence:** `build_write_tools` is called with `node_name="driver"`
from within `run_refactorer`, not `"refactorer"` — worth a comment at the
call site so it doesn't read as a bug.

### ADR-009: Per-checkpoint-type rejection counters
**Decision:** Interrupt trigger #1 (same root cause rejected twice) counts
separately per checkpoint type (RED-check, GREEN-check, refactor-check)
rather than one counter for the whole ticket. `RejectionRecord` gets a new
`checkpoint` field; `cycle_number` is computed per-checkpoint-type, not
globally.
**Reasoning:** A rejection during the RED checkpoint and a rejection
during the GREEN checkpoint aren't "the same kind of failure happening
twice" even if they happen to share a root-cause string — conflating them
would trigger the interrupt on unrelated coincidences instead of genuine
repeated failure at the same stage.
**Consequence:** This is a state schema change (additive field on
`RejectionRecord`), which is why it's captured as its own ADR rather than
folded silently into ADR-007 — worth a schema_version bump when BTN-12
lands, per ADR-001's versioned-contract discipline.

## Module Layout (updated)
```
battalion/
  ...
  nodes/
    architect.py
    driver.py       # gains mode: Literal["red", "green"] (BTN-11)
    reviewer.py      # gains expect_pass: bool (BTN-12)
    refactorer.py    # new (BTN-13) — shares driver's write_scope entry
    errors.py
  ...
```

## Sequencing (implementation order)
1. State models + persistence (nothing else can be built without this)
2. Per-node tool binding / write-scope mechanism
3. LiteLLM client wrapper
4. Architect node
5. Driver node (combined mode, per original BTN-5 scope)
6. Reviewer node (clean-tree verification, originally pass/fail-only)
7. **[REVISED]** Driver RED/GREEN mode support (BTN-11)
8. **[REVISED]** Reviewer expect_pass parameter + per-checkpoint rejection
   counters (BTN-12) — depends on the RejectionRecord schema change (ADR-009)
9. **[REVISED]** Refactorer node (BTN-13) — depends on BTN-11's mode pattern
10. Graph wiring (edges, node sequencing, interrupt points) — now must wire
    the full RED -> Reviewer -> GREEN -> Reviewer -> Refactorer -> Reviewer
    loop, not the original linear 3-node chain
11. Interrupt triggers (1-6) + budget tracking — trigger #1 now needs
    per-checkpoint counter logic per ADR-009
12. CLI (run / resume / status)
13. End-to-end acceptance criteria validation

Steps 1-11 are complete and shipped (BTN-1 through BTN-8, BTN-11, BTN-12,
BTN-13). Steps 7-9 were new, discovered during the architecture pass that
preceded graph wiring — deliberately sequenced before graph wiring
(originally step 7, now step 10) rather than after, since wiring the graph
against the old 3-node assumption would need redoing once these land. Steps
12 (CLI) and 13 (end-to-end acceptance validation) remain open — see
backlog.json BTN-9 and BTN-10.

## Risks / Watch Items
- **[RESOLVED during architecture pass, ADR-007]** Reviewer originally
  always treated "tests pass" as accept. This is wrong for the RED
  checkpoint, where a correctly-written failing test should be accepted.
  Caught before BTN-7 wired the graph against it — would have silently
  broken the RED checkpoint otherwise.
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
