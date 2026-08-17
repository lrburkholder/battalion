# BTN-40 Electron Desktop Architecture Spike

This directory is a disposable Electron prototype that consumes BTN-37's
fixture, scenario, validator, and measurement categories unchanged. It is
benchmark evidence, not production Battalion policy.

## Install and run

The repository's npm launcher is broken, so this spike uses the already
available Corepack-managed pnpm with Electron's required hoisted layout. Its
project-local `pnpm-workspace.yaml` also allows Electron's install script and
Forge's Git-hosted `@electron/node-gyp` transitive dependency:

```powershell
Set-Location C:\src\battalion\benchmarks\desktop\electron
pnpm install
Set-Location C:\src\battalion
.\.venv\Scripts\python.exe -m benchmarks.desktop.electron.prepare
Set-Location benchmarks\desktop\electron
pnpm start
```

Emit a trace and screenshot, then exit automatically:

```powershell
pnpm start -- --trace=evidence\trace.json --screenshot=evidence\electron-benchmark.png
Set-Location C:\src\battalion
.\.venv\Scripts\python.exe -m benchmarks.desktop.acceptance benchmarks\desktop\electron\evidence\trace.json
```

Package or make a ZIP archive:

```powershell
pnpm package
pnpm make
```

## Boundary and permissions

The renderer is sandboxed and context-isolated with Node integration disabled.
Its preload bridge exposes only fixture loading and completion reporting. The
main process rejects permission requests and cancels HTTP(S)/WebSocket traffic.
It reads only the packaged benchmark inputs and writes only explicit CLI trace
and screenshot destinations.

Electron's main process nevertheless carries ambient Node filesystem, process,
and network authority. The bridge and request filters mechanically narrow the
renderer, but the larger privileged process and packaged Chromium surface are
comparison evidence for BTN-41.
