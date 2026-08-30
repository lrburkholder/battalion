# Contributor setup

Source development uses an editable install. End users and release-gate UAT
must instead follow [Getting Started](getting-started.md) with an identified
wheel/ZIP so a checkout cannot hide missing runtime assets.

Use Python 3.11+ and Git. In PowerShell, from your chosen development directory:

```powershell
git clone https://github.com/lrburkholder/battalion.git
Set-Location battalion
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest
```

For desktop development only, install `.[desktop,dev]` instead; that adds the
Qt and Nuitka build dependencies. Follow the repository's `AGENTS.md`, canonical
ticket criteria, and [implementation plan](../plan.md). Unit tests must not
require provider credentials or real provider calls. Consult
[Releases and distribution](release.md) for maintainer packaging and tagging;
neither an editable install nor a test pass publishes an end-user artifact.
