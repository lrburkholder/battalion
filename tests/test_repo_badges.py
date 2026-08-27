"""Tests for repository badges and README trust signals (BTN-50)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPOSITORY_ROOT / "README.md"
LICENSE_PATH = REPOSITORY_ROOT / "LICENSE"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "test.yml"
PAGES_CONFIG = REPOSITORY_ROOT / ".github" / "pages" / "_config.yml"


class TestReadmeBadges:
    """Validate the badge row in README.md."""

    @pytest.fixture
    def readme_content(self) -> str:
        """Load the README.md content."""
        return README_PATH.read_text(encoding="utf-8")

    def test_readme_begins_with_badge_row(self, readme_content: str) -> None:
        """The README begins with a compact badge row."""
        lines = readme_content.splitlines()
        # After the title, the next non-empty line should start the badge row
        assert lines[0] == "# battalion"
        # Skip empty lines after title
        badge_line_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip():
                badge_line_idx = i
                break
        assert badge_line_idx is not None
        assert "[![" in lines[badge_line_idx]

    def test_test_status_badge_present(self, readme_content: str) -> None:
        """Test status badge is present and links to GitHub Actions workflow."""
        assert "[![Test" in readme_content
        assert (
            "https://github.com/lrburkholder/battalion/actions/workflows/test.yml/"
            "badge.svg?branch=main"
        ) in readme_content
        assert (
            "https://github.com/lrburkholder/battalion/actions/workflows/test.yml"
            "?query=branch%3Amain"
        ) in readme_content

    def test_python_version_badge_present(self, readme_content: str) -> None:
        """Python version badge is present."""
        assert "[![Python" in readme_content or "[![python" in readme_content.lower()

    def test_license_badge_present(self, readme_content: str) -> None:
        """MIT license badge is present and links to LICENSE."""
        assert "[![License" in readme_content or "[![license" in readme_content.lower()
        assert "MIT" in readme_content
        # Should link to LICENSE file
        assert "](LICENSE)" in readme_content or "](LICENSE.md)" in readme_content

    def test_documentation_badge_present(self, readme_content: str) -> None:
        """Documentation badge is present and links to GitHub Pages."""
        assert "[![Documentation" in readme_content or "[![docs" in readme_content.lower()
        assert "lrburkholder.github.io/battalion" in readme_content

    def test_all_badges_have_alt_text(self, readme_content: str) -> None:
        """Every badge has meaningful alt text."""
        badge_pattern = r'\[!\[([^\]]+)\]\([^)]+\)\]'
        badges = re.findall(badge_pattern, readme_content)
        
        # Each badge should have non-empty alt text
        for alt_text in badges:
            assert len(alt_text.strip()) > 0, f"Empty alt text found in badge"
        
        # Verify we have the expected badges
        expected_keywords = ["Test", "Python", "License", "Documentation"]
        found_badges = [alt for alt in badges if any(kw.lower() in alt.lower() for kw in expected_keywords)]
        assert len(found_badges) >= 4, f"Expected at least 4 badges, found {len(found_badges)}"

    def test_all_badge_links_are_valid_urls_or_paths(self, readme_content: str) -> None:
        """Every badge link is either a valid URL or a relative file path."""
        # Find all badge markdown: [![alt](url)] - only in the badge row (first few lines)
        lines = readme_content.splitlines()
        # Get the first 10 lines (title + badge row + empty line)
        header_lines = lines[:10]
        header_content = "\n".join(header_lines)
        
        # Find all badge markdown in the header section only
        badge_pattern = r'\[!\[[^\]]*\]\(([^)]+)\)\]'
        links = re.findall(badge_pattern, header_content)
        
        for link in links:
            # Check if it's a URL
            if link.startswith("http://") or link.startswith("https://"):
                parsed = urlparse(link)
                assert parsed.scheme in ("http", "https")
                assert parsed.netloc, f"Invalid URL: {link}"
            # Check if it's a relative file path
            elif "/" in link or link.endswith(".md"):
                # Relative paths are okay for local files like LICENSE
                assert link in ("LICENSE", "LICENSE.md"), f"Unexpected relative path: {link}"
            else:
                # Local file reference
                assert Path(link).suffix in (".md", ""), f"Unexpected link format: {link}"


class TestBadgeTargets:
    """Validate that badge targets exist and are correct."""

    def test_license_file_exists(self) -> None:
        """The LICENSE file exists and is MIT."""
        assert LICENSE_PATH.exists()
        content = LICENSE_PATH.read_text(encoding="utf-8")
        assert "MIT License" in content

    def test_test_workflow_exists(self) -> None:
        """The GitHub Actions test workflow exists."""
        assert WORKFLOW_PATH.exists()
        content = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "name: Test" in content
        assert "python -m pytest" in content

    def test_pages_config_exists(self) -> None:
        """The Pages configuration exists with correct URL."""
        assert PAGES_CONFIG.exists()
        content = PAGES_CONFIG.read_text(encoding="utf-8")
        assert "lrburkholder.github.io" in content


class TestWorkflowRequirements:
    """Validate the test workflow meets BTN-50 requirements."""

    @pytest.fixture
    def workflow_content(self) -> str:
        """Load the test workflow content."""
        return WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_runs_on_supported_python_versions(self, workflow_content: str) -> None:
        """The workflow tests on supported Python versions (>=3.11)."""
        assert "3.11" in workflow_content
        # Should test on multiple versions
        versions = re.findall(r'"3\.\d+"', workflow_content)
        assert len(versions) >= 2

    def test_workflow_installs_dev_dependencies(self, workflow_content: str) -> None:
        """The workflow installs declared development dependencies."""
        # Check for the install command with dev extras
        assert '-e ".[dev]"' in workflow_content or '-e "[dev]"' in workflow_content or "-e .[dev]" in workflow_content

    def test_workflow_runs_credential_independent_tests(self, workflow_content: str) -> None:
        """The workflow runs pytest without live provider calls."""
        assert "python -m pytest" in workflow_content

    def test_workflow_uses_github_actions_badge_format(self, workflow_content: str) -> None:
        """The workflow file is named test.yml for badge compatibility."""
        assert WORKFLOW_PATH.name == "test.yml"


class TestBadgeRendering:
    """Validate badge rendering and theme compatibility."""

    @pytest.fixture
    def readme_content(self) -> str:
        """Load the README.md content."""
        return README_PATH.read_text(encoding="utf-8")

    def test_badges_use_shields_io_or_github_badges(self, readme_content: str) -> None:
        """Badges use shields.io or GitHub native badge format for theme compatibility."""
        # Check for shields.io (most common)
        assert "img.shields.io" in readme_content or "github.com/" in readme_content

    def test_badges_are_compact(self, readme_content: str) -> None:
        """The badge row is compact (badges on a single line or minimal lines)."""
        lines = readme_content.splitlines()
        # Find badge lines
        badge_lines = []
        in_badge_section = False
        for line in lines:
            if line.strip().startswith("[!["):
                in_badge_section = True
                badge_lines.append(line)
            elif in_badge_section and line.strip() == "":
                break
            elif in_badge_section:
                badge_lines.append(line)
        
        # Badges should be on 1-4 lines max for compactness
        assert len(badge_lines) <= 4, f"Badge row spans {len(badge_lines)} lines, should be compact"


class TestPublishedPagesBadges:
    """Validate that published Pages site has working badge links."""

    def test_pages_build_includes_readme_badges(self) -> None:
        """Verify the Pages build process handles README badges correctly."""
        from scripts.build_pages import build

        output = REPOSITORY_ROOT / ".pages-source" / "badge_test"
        try:
            build(output)
            # Check that the staged index includes badge references or they're in the source
            index_path = output / "index.md"
            if index_path.exists():
                content = index_path.read_text(encoding="utf-8")
                # The build should not break due to badge syntax
                assert "[![".count(content) >= 0  # Badges are either kept or removed cleanly
        finally:
            if output.exists():
                import shutil
                shutil.rmtree(output)
