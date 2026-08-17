# BTN-39 PySide6 Desktop Architecture Spike

This directory is a disposable Qt Widgets prototype. It consumes BTN-37's
fixture, scenario, validator, and measurement categories without changing the
application contract. It is evidence, not production Battalion policy.

## Install and run

From the repository root on Python 3.11 or newer:

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r benchmarks\desktop\pyside6\requirements.txt
.\.venv\Scripts\python.exe -m benchmarks.desktop.pyside6.prepare
.\.venv\Scripts\python.exe -m benchmarks.desktop.pyside6.app --trace benchmarks\desktop\pyside6\evidence\trace.json
```

For a deterministic offscreen visual artifact:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m benchmarks.desktop.pyside6.app --trace benchmarks\desktop\pyside6\evidence\trace.json --screenshot benchmarks\desktop\pyside6\evidence\pyside6-benchmark.png
```

Validate the emitted trace in another terminal after closing the app:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.desktop.acceptance benchmarks\desktop\pyside6\evidence\trace.json
```

Run the framework-only automated tests with Qt's offscreen platform:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests\test_pyside6_spike.py -q
```

## Package

Qt's official deploy tool currently shells out to `python -m pip freeze`, so a
uv-created environment needs `pip` added as a packaging-only prerequisite:

```powershell
uv pip install --python .\.venv\Scripts\python.exe pip
Set-Location benchmarks\desktop\pyside6
..\..\..\.venv\Scripts\pyside6-deploy.exe -c pysidedeploy.spec --force
```

The pinned spec uses Python-3.14-compatible Nuitka 4.1.3 in standalone mode,
bundles the generated fixture and placeholder icon, and writes ignored
artifacts to `dist/`. On Windows the
deploy tool warns when `dumpbin` is unavailable outside a Visual Studio
developer shell; the required Qt modules are listed explicitly in the spec.
Nuitka's cached Dependency Walker download is made explicit with
`--assume-yes-for-downloads` so non-interactive builds are reproducible.
One-file mode was attempted first but exhausted available memory during
Zstandard payload compression; standalone mode preserves that evidence while
providing a lower-memory packaging path.

## Boundary and permissions

The UI imports only the framework-local adapter and reads explicit exported
fixture paths. It does not import Battalion graph, provider, persistence, or
worker modules. Simulated actions stay in adapter memory.

Unlike Tauri's renderer capability model, a PySide6 process inherits the
launching user's filesystem, process, and network permissions. The prototype
does not exercise those permissions, but Python/Qt does not mechanically deny
them. This is benchmark evidence for BTN-41, not a production authorization
decision.
