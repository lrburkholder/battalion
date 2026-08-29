from __future__ import annotations

import re
import shutil
import struct
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
            "_layouts/default.html",
            "assets/site.css",
            "assets/favicon.ico",
            "assets/mark-transparent.svg",
            "assets/screenshots/battalion-work.png",
            "assets/screenshots/battalion-history.png",
            "assets/screenshots/battalion-intel.png",
            "index.md",
            "plan.md",
            "spec.md",
            "docs/adrs/index.md",
            *{f"docs/adrs/adr{number:04d}.md" for number in range(1, 37)},
            "docs/rfcs/rfc0004.md",
            "docs/operator/workflow.md",
            "docs/operator/screens.md",
            "docs/operator/showcase.md",
            "docs/rfcs/rfc0005.md",
            "docs/rfcs/rfc0006.md",
            "docs/rfcs/rfc0007.md",
            "benchmarks/desktop/index.md",
            "benchmarks/desktop/tauri/findings.md",
            "benchmarks/desktop/pyside6/findings.md",
            "benchmarks/desktop/electron/findings.md",
            "docs/status.md",
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
        layout = (output / "_layouts" / "default.html").read_text(encoding="utf-8")
        plan = (output / "plan.md").read_text(encoding="utf-8")
        adr_index = (output / "docs" / "adrs" / "index.md").read_text(
            encoding="utf-8"
        )
        rfc = (output / "docs" / "rfcs" / "rfc0004.md").read_text(
            encoding="utf-8"
        )
        inference_rfc = (output / "docs" / "rfcs" / "rfc0005.md").read_text(
            encoding="utf-8"
        )
        integration_rfc = (output / "docs" / "rfcs" / "rfc0006.md").read_text(
            encoding="utf-8"
        )
        actor_rfc = (output / "docs" / "rfcs" / "rfc0007.md").read_text(
            encoding="utf-8"
        )

        assert index.startswith("---\ntitle: Human-directed software delivery\nlayout: default\n---")
        assert index.count("layout: default") == 1
        assert "assets/screenshots/battalion-work.png" in index
        assert "docs/operator/workflow.html" in index
        assert "Roadmap ≠ shipped behavior" in index
        assert "assets/favicon.ico" in layout
        assert "assets/mark-transparent.svg" in layout
        assert "docs/adrs/" in index
        assert "docs/adrs/index.html" in plan
        assert "adr0001.html" in adr_index
        assert "adr0001.md" not in adr_index
        assert "adr0018.html" in adr_index
        assert "adr0019.html" in adr_index
        assert "adr0020.html" in adr_index
        assert "adr0021.html" in adr_index
        assert "adr0022.html" in adr_index
        assert "adr0023.html" in adr_index
        assert "adr0024.html" in adr_index
        assert "adr0025.html" in adr_index
        assert "adr0026.html" in adr_index
        assert "../../benchmarks/desktop/index.html" in rfc
        assert "../adrs/adr0024.html" in inference_rfc
        assert "../adrs/adr0025.html" in integration_rfc
        assert "../adrs/adr0026.html" in actor_rfc
        assert "adr0027.html" in adr_index
        assert "adr0028.html" in adr_index
        assert "adr0029.html" in adr_index
        assert "adr0030.html" in adr_index
        assert "adr0031.html" in adr_index
        assert "adr0036.html" in adr_index
        status_page = (output / "docs" / "status.md").read_text(encoding="utf-8")
        assert "BEGIN GENERATED:backlog-delivery" in status_page
        assert "### Milestone overview" in status_page
        assert "| Milestone | State | Issues | Completed | Open | Cancelled | Progress |" in status_page
        landing = (output / "index.md").read_text(encoding="utf-8")
        assert "docs/status.html" in landing
        assert not (output / "backlog.json").exists()
        benchmark_index = (output / "benchmarks" / "desktop" / "index.md").read_text(
            encoding="utf-8"
        )
        assert "tauri/findings.html" in benchmark_index
        assert "pyside6/findings.html" in benchmark_index
        assert "electron/findings.html" in benchmark_index
        assert (output / "docs" / "rfcs" / "rfc0004.md").exists()
        assert (output / "docs" / "rfcs" / "rfc0005.md").exists()
        assert (output / "docs" / "rfcs" / "rfc0006.md").exists()
        assert (output / "docs" / "rfcs" / "rfc0007.md").exists()
        assert (output / "benchmarks" / "desktop" / "index.md").exists()
        assert (output / "docs" / "operator" / "showcase.md").exists()
        assert (output / "assets" / "favicon.ico").read_bytes() == (
            REPOSITORY_ROOT / "battalion" / "desktop" / "assets" / "favicon.ico"
        ).read_bytes()
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_published_screenshots_are_legible_bounded_pngs() -> None:
    screenshots = REPOSITORY_ROOT / "docs" / "assets" / "screenshots"
    for view in ("work", "history", "intel"):
        image = screenshots / f"battalion-{view}.png"
        payload = image.read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", payload[16:24])
        assert (width, height) == (1380, 860)
        assert len(payload) < 150_000


def test_showcase_fixture_and_documentation_are_public_safe() -> None:
    source = (
        REPOSITORY_ROOT / "battalion" / "desktop" / "demo.py"
    ).read_text(encoding="utf-8")
    procedure = (
        REPOSITORY_ROOT / "docs" / "ui" / "showcase.md"
    ).read_text(encoding="utf-8")
    combined = (source + procedure).lower()

    assert "showcase-operator" in combined
    assert "credential-free" in combined or "credentials" in combined
    assert "api_key" not in combined
    assert "c:\\users\\" not in combined
    assert "mockup-only" in combined


def test_staged_pages_have_no_broken_static_local_references() -> None:
    from scripts.build_pages import build

    output = REPOSITORY_ROOT / ".pages-source" / "links"
    try:
        build(output)
        references: list[tuple[Path, str]] = []
        pattern = re.compile(
            r'(?:href|src)="([^"#?]+)|\[[^\]]+\]\(([^)#?]+)'
        )
        for document in (*output.rglob("*.md"), *output.rglob("*.html")):
            for match in pattern.finditer(document.read_text(encoding="utf-8")):
                target = match.group(1) or match.group(2)
                if target.startswith(("http:", "https:", "mailto:", "{{")):
                    continue
                references.append((document, target))

        missing: list[str] = []
        for document, target in references:
            candidate = (
                output / target.lstrip("/")
                if target.startswith("/")
                else document.parent / target
            )
            if candidate.suffix == ".html":
                candidates = (
                    candidate,
                    candidate.with_suffix(".md"),
                    candidate / "index.md",
                )
            elif not candidate.suffix:
                candidates = (candidate, candidate / "index.md")
            else:
                candidates = (candidate,)
            if not any(item.exists() for item in candidates):
                missing.append(f"{document.relative_to(output)} -> {target}")

        assert missing == []
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
    assert workflow[True]["workflow_run"] == {
        "workflows": ["Complete merged Battalion ticket"],
        "types": ["completed"],
    }
    assert workflow["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "issues": "read",
        "pages": "read",
    }
    assert workflow["jobs"]["build"]["if"] == (
        "github.event_name != 'workflow_run' || "
        "github.event.workflow_run.conclusion == 'success'"
    )
    build_steps = workflow["jobs"]["build"]["steps"]
    assert {
        "name": "Render current project status from canonical GitHub Issues",
        "env": {"GH_TOKEN": "${{ github.token }}"},
        "run": "python scripts/sync_status.py",
    } in build_steps

    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["if"] == (
        "github.ref == 'refs/heads/main' && github.event_name != 'pull_request' && "
        "(github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success')"
    )
    assert deploy["permissions"] == {
        "actions": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert deploy["environment"]["name"] == "github-pages"


def test_test_workflow_is_hermetic() -> None:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["test"]["steps"]
    assert workflow["permissions"] == {"contents": "read"}
    assert all("sync_status.py" not in step.get("run", "") for step in steps)


def test_status_governance_workflow_tracks_canonical_lifecycle_events() -> None:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / ".github" / "workflows" / "status-governance.yml").read_text(
            encoding="utf-8"
        )
    )

    assert workflow["permissions"] == {"contents": "read", "issues": "read"}
    assert workflow[True]["issues"]["types"] == [
        "closed", "edited", "labeled", "milestoned", "opened", "reopened", "unlabeled", "demilestoned",
    ]
    assert {
        "name": "Validate canonical GitHub Issue corpus",
        "env": {"GH_TOKEN": "${{ github.token }}"},
        "run": "python scripts/sync_status.py --validate",
    } in workflow["jobs"]["validate"]["steps"]
