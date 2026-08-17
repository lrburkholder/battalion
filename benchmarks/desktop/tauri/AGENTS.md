# Tauri development guardrails

These instructions apply to work under `benchmarks/desktop/tauri/`. They are
provisional safeguards for learning through the BTN-38 spike; they do not
replace the framework-selection ADR required by BTN-41 or promote spike code
into production.

## Preserve the application boundary

- Python owns Battalion application policy, graph execution, persistence,
  interrupts, provider access, write scopes, and domain validation.
- Rust owns only desktop-shell concerns that need native authority: application
  lifecycle, child-process supervision, Tauri capabilities, and transport
  adaptation at the Python application boundary.
- The renderer owns presentation state and operator interaction. It must not
  acquire filesystem, shell, provider, graph, or persistence authority.
- Do not duplicate Python policy in Rust or renderer code. If a requested Rust
  change needs domain knowledge, extend the transport-neutral Python
  application boundary first or stop for an architectural decision.
- Treat messages across the Python/Rust and Rust/renderer boundaries as
  versioned contracts. Use named serializable types, structured errors, and
  explicit compatibility behavior instead of ad hoc JSON or string parsing.

## Keep native authority narrow

- Add Tauri permissions and plugins individually. Explain the exact command or
  resource each grant enables; do not add broad filesystem, shell, HTTP, or
  process permissions for convenience.
- Pass structured arguments directly to supervised child processes. Never
  interpolate operator or model input into a shell command.
- Track child-process identity and ownership explicitly. Cleanup may terminate
  only processes started and recorded by the application.
- Do not add `unsafe` Rust without a human-approved architectural decision that
  documents why safe Rust or an existing dependency cannot satisfy the need.
- Keep dependencies small and justified. Before adding one, check whether the
  Rust standard library, Tauri, Serde, or an existing Battalion dependency
  already provides the behavior.
- Avoid `unwrap`, `expect`, and deliberate panics in non-test request, IPC, and
  process-management paths. Return contextual, structured failures instead.

## Make Rust changes teachable

Before implementation, state why the behavior belongs in Rust and identify the
Python/Rust and Rust/renderer contract impact. At handoff, explain:

- the Rust concepts involved, including ownership, borrowing, lifetimes,
  traits, concurrency, or error propagation when relevant;
- which invariants the type system enforces and which still rely on tests or
  runtime checks;
- the permissions and native resources touched;
- alternatives considered and remaining uncertainty.

Prefer straightforward code that a learning human can trace over clever
abstractions. Generated code is not complete until the human-facing explanation
and repository documentation make its design reviewable.

## Validation gates

For Rust or Tauri behavior changes, run from `src-tauri/`:

```powershell
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets --all-features
```

Also run the shared BTN-37 contract and relevant Python tests from the
repository root. Add contract tests for message schemas, permission-sensitive
commands, process cleanup, malformed input, reconnect behavior, and structured
failures as those capabilities are introduced.

A human decision is required before broadening Tauri capabilities, moving
application policy out of Python, changing process ownership, adding `unsafe`,
or changing a cross-language contract incompatibly.
