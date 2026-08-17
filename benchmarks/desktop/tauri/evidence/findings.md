# BTN-38 Tauri Spike Evidence

Status: In progress  
Control fixture: `BTN-37-desktop-v1`  
Framework: Tauri 2, static ECMAScript frontend, Rust command host  
Environment date: 2026-08-17  
Environment: Windows, Node.js v24.2.0, rustc 1.97.1, Cargo 1.97.1,
tauri-cli 2.11.4

Observed native result: the Tauri window launched and rendered all 12 shared
scenario steps as complete. The exact accepted output is retained in
`trace.json`, and `tauri-benchmark.png` records the visible result supplied by
the operator.

This file follows the nine categories and observation names from BTN-37's
measurement template. Raw release measurements will be added only after the
native toolchain can produce a release build.

## Packaging

Observations: installed bytes; artifact count; clean-machine launch.

- Initial command attempts found neither Rust nor Cargo on `PATH`; after the
  prerequisite installation, the Rust tools are available under `.cargo/bin`.
- Command attempted: `npm --version`; result: installed launcher references a
  missing `npm-cli.js`.
- `cargo tauri icon app-icon.png` generated the required Windows `icon.ico`
  plus desktop macOS and PNG variants from one disposable source image;
  mobile and Store variants are excluded from this desktop spike.
- `cargo tauri build --no-bundle` succeeded in 1 minute 47 seconds and produced
  `battalion-tauri-spike.exe` at 8,632,320 bytes.
- Installer artifact count, hashes, and clean-machine launch remain pending a
  full bundled build.
- The prototype avoids an npm/bundler dependency; Cargo/Tauri CLI and Windows
  native prerequisites are still required.

## Process

Observations: process tree; worker isolation; orphan cleanup.

- Native process samples are pending benchmark collection against the release
  executable.
- Current prototype runs the provider-free fixture in the renderer and does
  not claim to implement production worker supervision.
- A production sidecar remains a replaceable application-boundary concern.

## Resource

Observations: startup time; idle working set; active working set; CPU.

- Five cold-start and five scenario samples are pending controlled collection.
- No development-server measurements will be substituted for release data.

## Accessibility

Observations: keyboard completion; focus order; screen-reader names; contrast.

- The shared scenario is exposed as a native ordered list with focusable
  results, landmark headings, a polite status region, and no pointer-only
  action.
- Keyboard-only and Windows screen-reader passes remain pending a measured
  native launch.
- Contrast automation remains pending.

## Testability

Observations: headless coverage; determinism; failure diagnostics.

- The pure ECMAScript adapter runs under Node without a webview or provider.
- Python tests export the authoritative BTN-37 input, run the adapter, and pass
  its trace back through BTN-37's unchanged validator.
- The operator-supplied native trace contains all 12 ordered steps and passes
  that same validator without modification.
- Malformed and reordered trace failures remain owned by the shared validator.

## Failure recovery

Observations: worker crash; client restart; missed transient event; malformed
fixture.

- Reconnect derives a barrier from the event stream but renders the durable
  checkpoint first, preserving BTN-36 semantics.
- Worker-crash and client-restart injection remain pending native process work.
- An unsupported fixture identity fails visibly instead of rendering stale
  state.

## Permission surface

Observations: filesystem grants; process grants; network grants; renderer
capabilities.

- Capability inventory: `core:default` only.
- No filesystem, shell, dialog, HTTP, or opener plugin is linked or granted.
- CSP limits `connect-src` to `'self'` so the renderer can fetch packaged
  fixture JSON but cannot connect to a remote origin.
- Effective release-permission probes remain pending a bundled release build.

## Learning

Observations: new concepts; blocked time; debugging time; confidence.

- New concepts encountered: Tauri capability manifests, CSP alongside IPC
  permissions, direct static `frontendDist`, and Rust command serialization.
- Initial blocker resolved: Rust/Cargo were installed and the native release
  executable now compiles.
- Secondary blocker avoided by design: broken npm installation; the spike uses
  browser-native modules instead of a bundler.
- Confidence: high in the isolated fixture boundary and base Windows compile;
  unmeasured for installers, sidecars, and cross-platform behavior.

## Implementation complexity

Observations: framework-specific LOC; boundary adapters; configuration files;
dependencies.

- Boundary adapters: one pure JavaScript fixture adapter and one static Rust
  metadata command.
- Rust dependencies: `tauri`, `serde`; build dependency: `tauri-build`.
- Frontend dependencies: none.
- Significant framework-specific files: `src-tauri/Cargo.toml`, `build.rs`,
  `src/main.rs`, `tauri.conf.json`, and `capabilities/main.json`.
- Exact tracked file and line counts will be recorded with the completed
  release evidence.
