# BTN-40 Electron Spike Evidence

Status: In progress  
Control fixture: `BTN-37-desktop-v1`  
Framework: Electron 43.4.0, Electron Forge 7.11.2, Node.js v24.2.0  
Environment date: 2026-08-17  
Environment: Windows

Observed native result: Electron's sandboxed renderer displayed all 12 shared
steps, the native main/preload/renderer path emitted an accepted `trace.json`,
and `electron-benchmark.png` records the rendered Chromium UI.

This record preserves BTN-37's nine categories and observation names.

## Packaging

Observations: installed bytes; artifact count; clean-machine launch.

- npm is broken on the workstation; Corepack/pnpm works without repairing it.
- Electron requires a physical hoisted `node_modules` layout for packaging.
- pnpm 11 blocks Forge's Git-hosted `@electron/node-gyp` transitive dependency
  by default. BTN-40 scopes `blockExoticSubdeps: false` and Electron's allowed
  install script to this disposable project rather than global configuration.
- Forge's unpacked Windows x64 package contains 75 files and is 365,521,443
  bytes. The launcher is 225,533,952 bytes (SHA-256
  `12CDE31EE51B4EC088041A231545AC63DECFB105C264AF5D831CA0F9F0187D49`).
- The ZIP distributable is 145,435,393 bytes (SHA-256
  `BF65F9C157EC16AE417B4AC3525398EF6593ACA33A54835E0FB573E3B62D3BDB`).
- A clean-machine launch was not performed; the package carries Electron and
  application dependencies rather than requiring a workstation Node install.

## Process

Observations: process tree; worker isolation; orphan cleanup.

- Electron introduces a privileged Node main process plus Chromium renderer
  and utility processes before any Battalion worker exists.
- The packaged idle app consistently used four processes: main, renderer, GPU,
  and network utility. All five samples returned the same process count.
- Automated runs use a unique temporary Chromium profile so cache locks cannot
  couple samples. The initial measurement method exposed that launching a
  Windows GUI executable directly from PowerShell returns before its process;
  retained processes were removed by exact executable path and all reported
  samples use `Start-Process -Wait`.

## Resource

Observations: startup time; idle working set; active working set; CPU.

- Packaged launch through rendering, shared scenario completion, trace write,
  screenshot capture, and clean process exit: 1,146.05, 788.58, 774.39,
  768.06, and 758.91 ms; median 774.39 ms. The first sample was retained as the
  cold-cache observation.
- Five 1.5-second idle process-tree samples: working set 370,696,192,
  372,338,688, 361,472,000, 361,644,032, and 372,994,048 bytes (median
  370,696,192); private bytes 218,251,264, 220,381,184, 208,556,032,
  209,375,232, and 220,594,176 (median 218,251,264).
- Active working set and CPU sampling remain pending.

## Accessibility

Observations: keyboard completion; focus order; screen-reader names; contrast.

- Semantic HTML, landmarks, focusable results, and a polite status region are
  present. Native keyboard and screen-reader passes remain pending.

## Testability

Observations: headless coverage; determinism; failure diagnostics.

- The pure ECMAScript adapter runs under Node without Chromium or a provider.
- The emitted trace is checked by BTN-37's unchanged validator.
- A trace emitted through the native preload/IPC path passes that validator.
- Capture or write failures are surfaced with a diagnostic and exit code 3;
  benchmark automation verifies both trace and screenshot files before
  accepting a sample.

## Failure recovery

Observations: worker crash; client restart; missed transient event; malformed
fixture.

- Reconnect projects durable state before the stream barrier.
- Unknown fixture identity fails explicitly.
- Main/renderer crash and restart injection remain to be measured.

## Permission surface

Observations: filesystem grants; process grants; network grants; renderer
capabilities.

- Renderer: sandboxed, context-isolated, Node integration disabled, CSP denies
  connections, remote request filter enabled, permission requests denied.
- Preload: two narrow methods (`load`, `complete`), no raw IPC exposure.
- Main: ambient Node filesystem/process/network authority remains substantial.

## Learning

Observations: new concepts; blocked time; debugging time; confidence.

- Framework concepts: main/renderer/preload separation, context bridge, IPC,
  Chromium request filtering, Forge packaging, and pnpm linker configuration.
- This spike deliberately uses plain ECMAScript, so TypeScript is not required
  to reach the contract. Adopting it for production would add compiler and type
  configuration but would improve validation across the IPC boundary.
- Renderer completion IPC can arrive before Chromium paints DOM updates;
  deterministic capture requires an explicit compositor settle.
- Web frontend familiarity reduces renderer learning but not privileged-boundary
  or process-model complexity.

## Implementation complexity

Observations: framework-specific LOC; boundary adapters; configuration files;
dependencies.

- Boundaries: main process, preload bridge, sandboxed renderer, adapter.
- Toolchain: Node, pnpm, Electron binary, Electron Forge, ZIP maker.
- Frontend has no bundler or UI-framework dependency.
- Significant implementation/configuration is 245 lines across main, preload,
  adapter, renderer HTML/ECMAScript/CSS, Forge configuration, and preparation
  code (tests, evidence, lockfile, and documentation excluded).
