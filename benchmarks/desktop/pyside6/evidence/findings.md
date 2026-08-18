# BTN-39 PySide6 Spike Evidence

Status: Complete with recorded limitations
Control fixture: `BTN-37-desktop-v1`  
Framework: PySide6 6.10.1, Qt Widgets, Python 3.14.6  
Environment date: 2026-08-17  
Environment: Windows

Observed native result: the Qt Widgets window rendered the accepted 12-step
scenario with native Windows fonts and controls. `trace.json` retains the exact
shared-validator output and `pyside6-benchmark.png` records the rendered UI.
The operator subsequently confirmed that the documented source-launch workflow
completed successfully on the benchmark workstation.

This record retains the nine categories and observation names from BTN-37's
measurement template. Release samples are not replaced with development-mode
estimates.

## Packaging

Observations: installed bytes; artifact count; clean-machine launch.

- PySide6 wheels include Qt binaries and installed successfully into the
  existing virtual environment.
- The official `pyside6-deploy` tool assumes `pip` is importable even in a
  uv-managed environment, so packaging required adding pip separately.
- A normal PowerShell session did not expose Visual Studio `dumpbin`; the
  reproducible deployment spec declares Core, Gui, and Widgets explicitly.
- PySide6 generated a Nuitka 2.7.11 pin that rejects Python 3.14. The spike
  overrides it with Nuitka 4.1.3, which recognizes Python 3.14.6 and the
  installed MSVC compiler.
- Nuitka one-file packaging on Windows also requires a cached Dependency
  Walker helper; non-interactive deployment must explicitly allow that
  download.
- The one-file compile reached payload creation but Zstandard failed with an
  allocation error. The reproducible spec therefore uses standalone mode;
  package size and artifact count will reflect the unpacked Qt distribution.
- Standalone deployment: 70 files totaling 97,025,381 bytes.
- Launcher: `app.exe`, 7,255,040 bytes, SHA-256
  `6A34E624E671AFD26DAA0E90366B9C03DD4FD45E7AB3D4A679B8364AEFAFA756`.
- A clean-machine launch was unavailable. The standalone artifact launched on
  the benchmark workstation, but portability to a second Windows image remains
  a production packaging risk for BTN-42.

## Process

Observations: process tree; worker isolation; orphan cleanup.

- The packaged prototype ran as one Python/Qt process during the idle samples.
- Production one-process-per-run supervision remains outside this fixture
  adapter and must use the accepted application boundary.

## Resource

Observations: startup time; idle working set; active working set; CPU.

- Packaged start-and-scenario samples (milliseconds): 853.09, 484.99, 473.79,
  482.44, 499.76. Median: 484.99 ms.
- One-second idle working-set samples (bytes): 82,944,000; 82,821,120;
  82,890,752; 82,771,968; 82,960,384. Median: 82,890,752 bytes.
- Idle private-byte samples (bytes): 43,876,352; 43,802,624; 43,257,856;
  43,765,760; 44,163,072. Median: 43,802,624 bytes.
- CPU-seconds-at-sample values: 0.328, 0.266, 0.281, 0.312, 0.297.

## Accessibility

Observations: keyboard completion; focus order; screen-reader names; contrast.

- Native Qt list and read-only text controls expose explicit accessible names
  and keyboard focus.
- Automated inspection, keyboard-only completion, and a Windows screen-reader
  pass were not recorded. Accessibility therefore remains unverified beyond
  the structural offscreen assertions and must be acceptance evidence for the
  production UI.

## Testability

Observations: headless coverage; determinism; failure diagnostics.

- The pure Python adapter is independently testable without Qt or a provider.
- Qt Widgets render offscreen for deterministic structural assertions.
- The emitted trace is checked by BTN-37's unchanged validator.
- A trace emitted by the packaged standalone executable also passes that
  validator.

## Failure recovery

Observations: worker crash; client restart; missed transient event; malformed
fixture.

- Reconnect renders the durable checkpoint before acknowledging the event
  barrier, matching the shared scenario.
- Unsupported fixture identity fails explicitly.
- The disposable adapter has no real worker to crash. Native worker-crash and
  client-restart injection were not measured and remain production risks rather
  than inferred passes.

## Permission surface

Observations: filesystem grants; process grants; network grants; renderer
capabilities.

- A Qt/Python process inherits the operator's ambient filesystem, process, and
  network authority; there is no renderer capability sandbox equivalent.
- The prototype adapter voluntarily reads only explicit fixture files and
  imports no provider or runtime authority, but that is convention rather than
  mechanical denial.

## Learning

Observations: new concepts; blocked time; debugging time; confidence.

- Qt Widgets maps directly onto the existing Python environment and required
  no cross-language serialization or web frontend toolchain.
- New framework concepts: widget ownership, layouts, Qt stylesheets, accessible
  item roles, and offscreen platform testing.
- Confidence is high for the Python application-boundary fit, deterministic
  fixture projection, and Windows workstation resource profile; low for
  clean-machine packaging and native accessibility until BTN-42 validates them.

## Implementation complexity

Observations: framework-specific LOC; boundary adapters; configuration files;
dependencies.

- Boundary adapters: one pure Python fixture projection.
- Framework dependency: pinned PySide6 meta-wheel plus its Essentials, Addons,
  and Shiboken wheels.
- UI structure: one imperative Qt Widgets module; no QML, browser renderer,
  JavaScript runtime, Rust host, or frontend bundler.

## Reproduction

From the repository root:

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r benchmarks\desktop\pyside6\requirements.txt
.\.venv\Scripts\python.exe -m benchmarks.desktop.pyside6.prepare
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests\test_pyside6_spike.py -q
.\.venv\Scripts\python.exe -m benchmarks.desktop.pyside6.app --trace benchmarks\desktop\pyside6\evidence\trace.json --screenshot benchmarks\desktop\pyside6\evidence\pyside6-benchmark.png
.\.venv\Scripts\python.exe -m benchmarks.desktop.acceptance benchmarks\desktop\pyside6\evidence\trace.json
```

Packaging requires the additional `pip` prerequisite and `pyside6-deploy`
command documented in the spike README. The retained numbers are packaged
release measurements; the commands above reproduce the contract, test, and
rendering paths without claiming a second-machine packaging result.
