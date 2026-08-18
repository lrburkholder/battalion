# BTN-38 Tauri Spike Evidence

Status: Complete with recorded limitations
Control fixture: `BTN-37-desktop-v1`
Framework: Tauri 2.11.5, static ECMAScript frontend, Rust command host
Environment date: 2026-08-17
Environment: Windows 11 Home build 26200, AMD Ryzen 7 7745HX, 16 logical
processors, 16,308,531,200 bytes visible memory, WebView2 151.0.4129.86,
Node.js 24.2.0, rustc/Cargo 1.97.1, tauri-cli 2.11.4

The release application rendered and exposed all 12 shared scenario steps, and
its trace passes BTN-37's unchanged validator. Raw samples are retained in
`measurements.json`; `tauri-benchmark.png` records the native result.

The benchmark is complete even where the outcome is negative: this disposable
spike has no worker sidecar to crash, no clean machine was available, and no
audio Narrator pass was performed. Those limitations are decision evidence and
must not be silently upgraded into production confidence.

## Packaging

Observations: installed bytes; artifact count; clean-machine launch.

- Exact build: prepare from the repository root with
  `python -m benchmarks.desktop.tauri.prepare`, then run `cargo tauri build`
  from `benchmarks/desktop/tauri/src-tauri`.
- The initially documented path invocation of `prepare.py` failed with
  `ModuleNotFoundError`; the README now uses the reproducible module entry
  point.
- The first full bundle build downloaded verified WiX 3.14.1 and NSIS 3.11
  tooling. Subsequent builds reused the cache.
- Release payload: `battalion-tauri-spike.exe`, 8,697,344 bytes, SHA-256
  `1346DA64AB9FB260D15F2855B1F2F6F0C89EAD4DDDAF73BA9A6AE64744AAEB69`.
- MSI: 2,895,872 bytes, SHA-256
  `B93B9A379EE31AD78343C7E084CC91488D456C43C9374A4289F095F3E0147D6F`.
- NSIS setup executable: 1,901,684 bytes, SHA-256
  `D5CF24E2A886AB90408E25B807BFDD85D9834600CE053A2C1814A9E743FA2104`.
- Artifact count is three: release executable, MSI, and NSIS installer. The
  application payload is the 8,697,344-byte executable; the installed WebView2
  runtime is supplied by Windows rather than bundled into this count.
- Clean-machine launch was unavailable on this workstation. The installers
  were produced but not installed on a second Windows image.

## Process

Observations: process tree; worker isolation; orphan cleanup.

- At 1.5 seconds idle, all five samples contained seven processes: one Tauri
  host and six WebView2 processes (browser, crash handler, GPU, network,
  storage, and renderer).
- The initial release omitted Tauri's standard Windows GUI subsystem attribute
  and produced an eighth `conhost.exe`; that spike defect was corrected before
  final measurements.
- Killing the Tauri main process in a seven-process tree left zero captured
  descendants alive after one second.
- No worker exists in this fixture-only spike, so worker isolation and
  worker-crash recovery are unsupported rather than inferred from client
  cleanup. Production Python-sidecar supervision remains unverified.

## Resource

Observations: startup time; idle working set; active working set; CPU.

- Five release starts to a native window: 136.57, 103.44, 106.59, 91.31, and
  91.24 ms; median 103.44 ms.
- Five release runs through DOM paint and the native completion acknowledgement:
  592.14, 654.20, 620.63, 609.83, and 661.65 ms; median 620.63 ms.
- Five 1.5-second idle working sets: 432,709,632, 423,882,752, 444,678,144,
  423,735,296, and 437,415,936 bytes; median 432,709,632 bytes. Median private
  bytes were 248,590,336.
- Completion-time working sets: 136,921,088, 223,322,112, 296,214,528,
  145,629,184, and 294,678,528 bytes; median 223,322,112 bytes. WebView child
  startup is asynchronous, so the raw completion snapshots intentionally show
  four to six processes and substantial variance.
- Median cumulative CPU at the 1.5-second idle sample was 1.4219 seconds;
  median cumulative CPU at renderer completion was 1.5156 seconds. These are
  process-tree CPU totals since launch, not steady-state CPU percentages.

## Accessibility

Observations: keyboard completion; focus order; screen-reader names; contrast.

- The scenario requires no pointer action and completed automatically. Windows
  UI Automation exposed all 12 list items as keyboard focusable in scenario
  order, followed by the acceptance trace.
- The native WebView accessibility tree exposed the document title,
  `12 of 12 steps complete`, both named regions, all 12 named list items, and
  the trace text.
- Calculated WCAG contrast ratios range from 8.79:1 for the focus outline to
  15.70:1 for primary text. Completion, trace, eyebrow, and error colors all
  exceed 9:1 against their configured backgrounds.
- A browser preview confirmed the static banner, heading hierarchy, status
  role, and named regions. It did not execute the Tauri module and was not used
  as evidence for completed list behavior.
- A Windows UI Automation screen-reader inspection was completed; an audible
  Narrator pass was not.

## Testability

Observations: headless coverage; determinism; failure diagnostics.

- The pure ECMAScript adapter runs under Node without a WebView or provider.
- Rust unit tests cover normal and measurement launch modes. Python tests
  export the authoritative bundle and run the shared and failure adapters.
- The unchanged BTN-37 acceptance validator passed three consecutive runs.
- Unsupported fixture identity, readiness-file failure, and permission-probe
  failure use explicit diagnostics. All tests are credential-free.

## Failure recovery

Observations: worker crash; client restart; missed transient event; malformed
fixture.

- Worker crash: unsupported because the spike has no Python worker or sidecar.
  This is an unresolved production risk, not a passing simulation.
- Client crash/restart: force-stopping the main process left zero descendants;
  restart reloaded the packaged durable fixture and completed all 12 steps in
  753.24 ms.
- Missed transient event: a durable-only injected observation set recovered
  phase `driver_green` at barrier sequence 3 with `durable_first: true`.
- Malformed fixture: identity `malformed` was rejected explicitly as
  `unsupported fixture: malformed`.

## Permission surface

Observations: filesystem grants; process grants; network grants; renderer
capabilities.

- Capability inventory remains `core:default`; no filesystem, shell, HTTP,
  dialog, or opener plugin is linked or granted.
- The opt-in release probe confirmed that an undeclared filesystem command, an
  undeclared shell command, and outbound `fetch` were all denied.
- CSP retains `connect-src 'self'` and no remote origin.
- The Rust host retains ambient native filesystem and process authority. The
  measurement file path comes only from a launcher argument and is never
  renderer-selected; the renderer submits a typed result containing booleans.

## Learning

Observations: new concepts; blocked time; debugging time; confidence.

- New concepts: Rust ownership and typed errors, managed Tauri state, commands,
  capability manifests, CSP, Windows WebView2 accounting, WiX, and NSIS.
- Tooling blockers: Rust/Cargo were initially absent from `PATH`, npm was
  broken, the icon was required for Windows resources, and the original
  preparation command was not import-safe.
- Framework debugging found two benchmark-specific issues: renderer completion
  can precede compositor paint, and the release GUI-subsystem attribute is
  required to avoid an unnecessary console host.
- The spike did not begin with a timestamped learning log, so exact human
  documentation and debugging minutes cannot be reconstructed honestly. Known
  command times are retained in `measurements.json`.
- Confidence is high for the fixture boundary, Windows packaging, capability
  denial, and accessibility semantics; low for a real Python sidecar and
  non-Windows targets.

## Implementation complexity

Observations: framework-specific LOC; boundary adapters; configuration files;
dependencies.

- Ten significant framework-only implementation/configuration files contain
  387 lines, excluding tests, evidence, generated icons, lockfiles, and the
  measurement harness.
- Boundaries: one pure ECMAScript fixture adapter plus typed Rust boundary and
  measurement commands. Battalion application policy remains in Python.
- Direct Rust dependencies are `tauri` and `serde`; build dependency is
  `tauri-build`. The frontend has no package or bundler dependency.
- Maintained configuration comprises `Cargo.toml`, `build.rs`,
  `tauri.conf.json`, and `capabilities/main.json`; Windows bundles additionally
  depend on cached WiX and NSIS tooling.

## Reproduction

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.desktop.tauri.prepare
Set-Location benchmarks\desktop\tauri\src-tauri
& "$env:USERPROFILE\.cargo\bin\cargo.exe" tauri build
Set-Location ..
.\measure.ps1
```

Rust and shared validation commands are documented in the spike README. The
measurement harness launches only the release executable, records five samples
per required resource path, performs denial and restart probes, and prints the
raw JSON used here.
