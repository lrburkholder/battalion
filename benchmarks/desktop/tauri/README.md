# BTN-38 Tauri Desktop Architecture Spike

This directory is a disposable Tauri 2 prototype. It is benchmark evidence,
not production Battalion code. The renderer consumes BTN-37's generated JSON
and the shared acceptance validator without changing either contract.

Work in this subtree must follow the scoped [Tauri development guardrails](AGENTS.md).
They keep Rust limited to a small native shell, preserve Python as the owner of
Battalion application policy, and require Rust changes to be explained as
human learning artifacts. They are provisional until BTN-41 records the
framework decision in an accepted ADR.

## Current state

The framework-neutral scenario adapter and Tauri shell are implemented. The
adapter completes all twelve shared scenario steps and produces an accepted
trace. A native optimized Windows executable now builds successfully with the
generated placeholder icon. Installer, runtime-resource, accessibility, and
failure-recovery measurements remain, so BTN-38 is still in progress.

## Prepare and test the shared scenario

From the repository root:

```powershell
.\.venv\Scripts\python.exe benchmarks\desktop\tauri\prepare.py
node benchmarks\desktop\tauri\tests\run-contract.mjs
.\.venv\Scripts\python.exe -m benchmarks.desktop.acceptance benchmarks\desktop\tauri\evidence\trace.json
```

`prepare.py` is the only fixture preparation path. It calls BTN-37's
`write_bundle`; it does not copy or reinterpret the scenario. Generated input
under `ui/benchmark-input/` and the trace are intentionally untracked.

## Run and package Tauri

Install the official prerequisites for Tauri 2 on Windows (Rust with the MSVC
toolchain, Microsoft C++ Build Tools, and WebView2). Then, from `src-tauri`:

```powershell
cargo install tauri-cli --version "^2" --locked
cargo tauri dev
cargo tauri build
```

The frontend has no package-manager or bundler dependency. `frontendDist`
points directly at `ui/`; preparing the fixture is therefore the only prebuild
step. Release artifacts are written below Cargo's ignored `target/` directory.
The checked-in `app-icon.png` is the disposable source for the desktop icon
set; regenerate it with `cargo tauri icon app-icon.png`. The spike retains the
configured Windows, macOS, and Linux outputs and discards generated mobile and
Store variants.

## Security and authority boundary

The renderer has no shell, filesystem, dialog, or network plugin. Its only
capability is `core:default`; CSP disables outbound connections. The sole Rust
command returns static boundary metadata and cannot mutate Battalion state.
All simulated operator actions remain renderer-local fixture projections.

Do not add application policy, graph access, provider calls, credentials, or
repository writes to this spike. A later production UI must replace the
fixture adapter with the accepted Battalion application boundary.
