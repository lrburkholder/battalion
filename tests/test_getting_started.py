"""Exercise operator commands from the guide without a provider or checkout install."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from uuid import UUID

import pytest
from typer.testing import CliRunner

from battalion.cli import app
from functools import partial


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/getting-started.md"
OPERATOR_DOCS = [
    GUIDE, ROOT / "docs/troubleshooting.md", ROOT / "docs/data-handling.md",
    ROOT / "docs/uat/cli.md", ROOT / "docs/uat/desktop.md",
]
BLOCKS = dict(re.findall(
    r"<!-- check:([\w-]+) -->\s*```powershell\n(.*?)\n```",
    GUIDE.read_text(encoding="utf-8"), re.DOTALL,
))
RECOVERY_BLOCKS = dict(re.findall(
    r"<!-- check:([\w-]+) -->\s*```powershell\n(.*?)\n```",
    (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8"), re.DOTALL,
))


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script: str, executable: str = "pwsh") -> subprocess.CompletedProcess:
    command = shutil.which(executable)
    if command is None:
        pytest.skip(f"{executable} is unavailable")
    # A pwsh parent exports its module path; Windows PowerShell must compute
    # its own so built-in cmdlets such as Get-FileHash remain discoverable.
    env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    return subprocess.run(
        [command, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60, env=env,
    )


@pytest.mark.parametrize("executable", ["pwsh", "powershell"])
def test_documented_powershell_blocks_parse(tmp_path, executable):
    """Catch shell mismatches and unquoted UUID placeholders in actual prose."""
    scripts = []
    for document in [*OPERATOR_DOCS, ROOT / "docs/contributing.md", ROOT / "README.md"]:
        for block in re.findall(r"```powershell\n(.*?)\n\s*```", document.read_text(encoding="utf-8"), re.DOTALL):
            scripts.append({"document": document.name, "script": block})
    source = tmp_path / "commands.json"
    source.write_text(json.dumps(scripts), encoding="utf-8")
    result = powershell(f"""
$ErrorActionPreference = 'Stop'
foreach ($Block in (Get-Content -LiteralPath {ps_quote(source)} -Raw | ConvertFrom-Json)) {{
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseInput($Block.script, [ref]$Tokens, [ref]$Errors) | Out-Null
    if ($Errors.Count) {{ throw ($Block.document + ': ' + ($Errors | Out-String)) }}
}}
""", executable)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("executable", ["pwsh", "powershell"])
def test_documented_checksum_rejects_changed_missing_and_duplicate_entries(tmp_path, executable):
    artifact = tmp_path / "candidate [1].whl"
    sums = tmp_path / "SHA256SUMS.txt"
    artifact.write_bytes(b"candidate bytes")
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    for lines, valid in [
        ([f"{checksum}  {artifact.name}"], True),
        ([f"{checksum} *{artifact.name}"], True),
        ([f"{'0' * 64}  {artifact.name}"], False),
        ([f"{checksum}  other.whl"], False),
        ([f"{checksum}  {artifact.name}"] * 2, False),
    ]:
        sums.write_text("\n".join(lines), encoding="utf-8")
        result = powershell(
            "$ErrorActionPreference = 'Stop'\n"
            f"$Wheel = {ps_quote(artifact)}\n$WheelSums = {ps_quote(sums)}\n"
            + BLOCKS["checksum"], executable,
        )
        assert (result.returncode == 0) == valid, result.stdout + result.stderr


def cli_arguments(block: str, variables: dict[str, str]) -> list[list[str]]:
    """Extract real CLI examples; substitute only guide-established PS variables."""
    commands = []
    for line in block.replace("`\n", " ").splitlines():
        prefix = "& $Python -m battalion "
        line = line.strip()
        if line.startswith(prefix):
            commands.append([
                variables.get(token, token)
                for token in shlex.split(line[len(prefix):])
            ])
    return commands


@pytest.mark.parametrize("executable", ["pwsh", "powershell"])
def test_guide_project_setup_run_uuid_inspect_and_resume(tmp_path, monkeypatch, executable):
    # Execute the documented project creation, then the documented CLI arguments.
    result = powershell(
        "$ErrorActionPreference = 'Stop'\n"
        f"$Lab = {ps_quote(tmp_path)}\n" + BLOCKS["project"], executable,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    project = next(tmp_path.glob("hello-project-*"))
    monkeypatch.chdir(project)
    for name in list(os.environ):
        if name.startswith("BATTALION_"):
            monkeypatch.delenv(name)
    import battalion.setup as setup_module
    checks = []
    monkeypatch.setattr(setup_module, "validate_connectivity", lambda model, **kwargs: checks.append(model))
    models = {
        "$ArchitectModel": "ollama/doc-architect",
        "$DriverModel": "ollama/doc-driver",
        "$ReviewerModel": "ollama/doc-reviewer",
        "$RefactorerModel": "ollama/doc-driver",
    }
    runner = CliRunner()
    setup = runner.invoke(app, cli_arguments(BLOCKS["setup"], models)[0])
    assert setup.exit_code == 0, setup.output
    assert checks == ["ollama/doc-architect"]  # one connectivity check per provider
    assert (project / "src/greeting.py").is_file()

    import battalion.nodes.architect as architect
    import battalion.nodes.driver as driver
    import battalion.nodes.reviewer as reviewer
    import battalion.nodes.refactorer as refactorer

    responses = {
        "architect": iter(["# Plan\nImplement greet within src/ and review pytest evidence."]),
        "driver": iter([
            json.dumps({"files": {"test_greeting.py": (
                'from greeting import greet\n\n'
                'def test_ada():\n    assert greet("Ada") == "Hello, Ada!"\n\n'
                'def test_empty():\n    assert greet("") == "Hello, !"\n'
            )}}),
            json.dumps({"files": {"greeting.py": 'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n'}}),
        ]),
        "refactorer": iter([json.dumps({"outcome": "no-change", "files": {}, "reason": "Already simple"})]),
    }

    def completion(role, *args, **kwargs):
        assert role in responses, f"Unexpected model invocation: {role}"
        return {"choices": [{"message": {"content": next(responses[role])}}]}

    with monkeypatch.context() as nodes:
        for module, name in [
            (architect, "run_architect"), (driver, "run_driver"),
            (reviewer, "run_reviewer"), (refactorer, "run_refactorer"),
        ]:
            nodes.setattr(module, name, partial(getattr(module, name), call_llm_fn=completion))
        started = runner.invoke(app, cli_arguments(BLOCKS["run"], {})[0])
        assert started.exit_code == 0, started.output
        run_id = re.search(r"Run complete: ([0-9a-f-]{36})", started.output).group(1)
        assert UUID(run_id).version == 4
        variables = {"$RunId": run_id}
        status = runner.invoke(app, cli_arguments(BLOCKS["status"], variables)[0])
        assert status.exit_code == 0, status.output
        assert "awaiting-human" in status.output
        assert "manual-checkpoint" in status.output
        for command in cli_arguments(BLOCKS["resume"], variables):
            resumed = runner.invoke(app, command)
            assert resumed.exit_code == 0, resumed.output
        evidence = runner.invoke(app, cli_arguments(BLOCKS["evidence"], variables)[0])
    state = json.loads(evidence.output)
    assert state["run_id"] == run_id
    assert state["status"] == "done"
    assert state["spec"].lstrip("\ufeff").startswith("# Greeting ticket")
    assert state["interrupt_log"][-1]["resolution"] == (
        "Reviewed the greeting plan and approved continuation within src/"
    )
    assert (project / "plan.md").is_file()
    assert (project / "src/test_greeting.py").is_file()
    # Real Reviewer subprocesses executed RED, GREEN, and refactor; only model
    # output/connectivity was substituted. The stub prevents collection errors.
    executions = state["execution_record"]["node_executions"]
    assert len(executions) == 7


def test_all_operator_cli_examples_accept_their_documented_options(monkeypatch, tmp_path):
    """--help validates parsing/options without running live setup or execution."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    commands = []
    for document in OPERATOR_DOCS:
        for block in re.findall(r"```powershell\n(.*?)\n\s*```", document.read_text(encoding="utf-8"), re.DOTALL):
            commands.extend(cli_arguments(block, {"$RunId": "00000000-0000-4000-8000-000000000001"}))
    assert {cmd[0] for cmd in commands} == {"run", "resume", "status", "setup", "--help"}
    for command in commands:
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0, (command, result.output)


def test_onboarding_publication_inputs_trigger_pages_and_test_workflows():
    import yaml
    for workflow in ["pages.yml", "test.yml"]:
        data = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
        for event in ["push", "pull_request"]:
            paths = data[True][event]["paths"]
            assert "docs/getting-started.md" in paths
            assert "docs/data-handling.md" in paths
            assert "docs/troubleshooting.md" in paths
            assert "docs/uat/**" in paths


@pytest.mark.parametrize("executable", ["pwsh", "powershell"])
@pytest.mark.parametrize("destination", ["private", "inside-project", "missing-state"])
def test_documented_backup_preserves_bytes_and_excludes_unrelated_evidence(tmp_path, executable, destination):
    project = tmp_path / "project [1]"
    run_id = "00000000-0000-4000-8000-000000000001"
    expected = {f"state/{run_id}.json", f"workers/{run_id}.json", "actors.json", "runs.json", "project.json"}
    for relative in expected | {"state/another-run.json", "traces/private.jsonl", "intel/private.md"}:
        path = project / ".battalion" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Even malformed state must be preserved byte-for-byte for diagnosis.
        path.write_bytes(b"original diagnostic bytes: " + relative.encode())
    if destination == "missing-state":
        (project / ".battalion/state" / f"{run_id}.json").unlink()
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    backup_parent = project if destination == "inside-project" else tmp_path / "private [backup]"
    backup_parent.mkdir(exist_ok=True)
    result = powershell(
        "$ErrorActionPreference = 'Stop'\n"
        f"$Project = {ps_quote(project)}\n$RunId = {ps_quote(run_id)}\n"
        f"function Read-Host {{ param($Prompt) {ps_quote(backup_parent)} }}\n"
        + RECOVERY_BLOCKS["backup"], executable,
    )
    assert (result.returncode == 0) == (destination == "private"), result.stdout + result.stderr
    assert {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
    backups = list(backup_parent.glob("battalion-recovery-*"))
    if destination == "private":
        assert len(backups) == 1
        actual = {p.relative_to(backups[0]).as_posix(): p.read_bytes() for p in backups[0].rglob("*") if p.is_file()}
        assert set(actual) == expected
        for relative, content in actual.items():
            assert content == before[Path(".battalion") / relative]
    else:
        assert backups == []
