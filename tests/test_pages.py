from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_pages_builder_stages_only_approved_content() -> None:
    output = REPOSITORY_ROOT / ".pages-source" / "test"
    try:
        subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts" / "build_pages.py"),
                "--output",
                str(output),
            ],
            check=True,
            cwd=REPOSITORY_ROOT,
        )

        staged = {
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file()
        }
        assert staged == {
            "LICENSE",
            "_config.yml",
            "index.md",
            "plan.md",
            "spec.md",
            "docs/adrs/index.md",
            *{f"docs/adrs/adr{number:04d}.md" for number in range(1, 24)},
            "docs/rfcs/rfc0004.md",
            "benchmarks/desktop/index.md",
            "benchmarks/desktop/tauri/findings.md",
            "benchmarks/desktop/pyside6/findings.md",
            "benchmarks/desktop/electron/findings.md",
        }
        assert all(
            not ({"recon", "intel", "ui"} & set(Path(path).parts))
            for path in staged
        )
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_pages_builder_prepares_markdown_for_jekyll() -> None:
    output = REPOSITORY_ROOT / ".pages-source" / "test"
    try:
        subprocess.run(
            [sys.executable, "scripts/build_pages.py", "--output", str(output)],
            check=True,
            cwd=REPOSITORY_ROOT,
        )

        index = (output / "index.md").read_text(encoding="utf-8")
        plan = (output / "plan.md").read_text(encoding="utf-8")
        adr_index = (output / "docs" / "adrs" / "index.md").read_text(
            encoding="utf-8"
        )
        rfc = (output / "docs" / "rfcs" / "rfc0004.md").read_text(
            encoding="utf-8"
        )

        assert index.startswith("---\nlayout: default\n---")
        assert "docs/adrs/index.html" in index
        assert "docs/adrs/index.html" in plan
        assert "adr0001.html" in adr_index
        assert "adr0001.md" not in adr_index
        assert "adr0018.html" in adr_index
        assert "adr0019.html" in adr_index
        assert "adr0020.html" in adr_index
        assert "adr0021.html" in adr_index
        assert "adr0022.html" in adr_index
        assert "adr0023.html" in adr_index
        assert "../../benchmarks/desktop/index.html" in rfc
        benchmark_index = (output / "benchmarks" / "desktop" / "index.md").read_text(
            encoding="utf-8"
        )
        assert "tauri/findings.html" in benchmark_index
        assert "pyside6/findings.html" in benchmark_index
        assert "electron/findings.html" in benchmark_index
        assert (output / "docs" / "rfcs" / "rfc0004.md").exists()
        assert (output / "benchmarks" / "desktop" / "index.md").exists()
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_pages_workflow_limits_deployment_permissions() -> None:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / ".github" / "workflows" / "pages.yml").read_text(
            encoding="utf-8"
        )
    )

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "pages": "read",
    }

    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["if"] == (
        "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
    )
    assert deploy["permissions"] == {
        "actions": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert deploy["environment"]["name"] == "github-pages"
