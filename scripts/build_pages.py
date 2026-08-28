"""Stage the explicitly approved documentation for GitHub Pages."""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Keep this list explicit. Adding a document to the repository must not make it
# public automatically.
PUBLISHED_DOCUMENTS = {
    ".github/pages/index.md": "index.md",
    "spec.md": "spec.md",
    "plan.md": "plan.md",
    "docs/status.md": "docs/status.md",
    "docs/release.md": "docs/release.md",
    "docs/adrs/README.md": "docs/adrs/index.md",
    **{
        f"docs/adrs/adr{number:04d}.md": f"docs/adrs/adr{number:04d}.md"
        for number in range(1, 32)
    },
    "docs/rfcs/rfc0004.md": "docs/rfcs/rfc0004.md",
    "docs/ui/workflow.md": "docs/operator/workflow.md",
    "docs/ui/screen-runs.md": "docs/operator/screens.md",
    "docs/ui/showcase.md": "docs/operator/showcase.md",
    "docs/rfcs/rfc0005.md": "docs/rfcs/rfc0005.md",
    "docs/rfcs/rfc0006.md": "docs/rfcs/rfc0006.md",
    "docs/rfcs/rfc0007.md": "docs/rfcs/rfc0007.md",
    "benchmarks/desktop/README.md": "benchmarks/desktop/index.md",
    "benchmarks/desktop/tauri/evidence/findings.md": "benchmarks/desktop/tauri/findings.md",
    "benchmarks/desktop/pyside6/evidence/findings.md": "benchmarks/desktop/pyside6/findings.md",
    "benchmarks/desktop/electron/evidence/findings.md": "benchmarks/desktop/electron/findings.md",
}

SUPPORTING_FILES = {
    "LICENSE": "LICENSE",
    ".github/pages/_config.yml": "_config.yml",
    ".github/pages/_layouts/default.html": "_layouts/default.html",
    ".github/pages/assets/site.css": "assets/site.css",
    "battalion/desktop/assets/favicon.ico": "assets/favicon.ico",
    "battalion/desktop/assets/mark-transparent.svg": "assets/mark-transparent.svg",
    "docs/assets/screenshots/battalion-work.png": "assets/screenshots/battalion-work.png",
    "docs/assets/screenshots/battalion-history.png": "assets/screenshots/battalion-history.png",
    "docs/assets/screenshots/battalion-intel.png": "assets/screenshots/battalion-intel.png",
}

MARKDOWN_LINK = re.compile(
    r"(?P<prefix>\[[^]]+\]\()(?P<target>[^)#]+\.(?:md|json))(?P<suffix>(?:#[^)]+)?\))"
)


def _rewrite_markdown_links(content: str, source: str) -> str:
    """Point links at the HTML paths produced by Jekyll."""

    source_directory = Path(source).parent

    def replace(match: re.Match[str]) -> str:
        raw_target = match.group("target")
        source_relative = posixpath.normpath(
            (source_directory / raw_target).as_posix()
        )
        destination = PUBLISHED_DOCUMENTS.get(source_relative)
        if destination is None:
            # Links may also reference published files from the repository
            # root regardless of the referencing document's location.
            root_relative = posixpath.normpath(raw_target)
            destination = PUBLISHED_DOCUMENTS.get(root_relative)
        if destination is None:
            return match.group(0)
        if destination.endswith(".md"):
            link_target = str(Path(destination).with_suffix(".html"))
        else:
            link_target = destination
        current_destination = Path(PUBLISHED_DOCUMENTS[source]).parent.as_posix()
        relative_target = posixpath.relpath(
            link_target.replace("\\", "/"), current_destination
        )
        return f'{match.group("prefix")}{relative_target}{match.group("suffix")}'

    return MARKDOWN_LINK.sub(replace, content)


def build(output: Path) -> None:
    """Create a clean Jekyll source tree containing only approved files."""

    output = output.resolve()
    if output == REPOSITORY_ROOT or REPOSITORY_ROOT not in output.parents:
        raise ValueError("output must be a directory inside the repository")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for source, destination in PUBLISHED_DOCUMENTS.items():
        content = (REPOSITORY_ROOT / source).read_text(encoding="utf-8")
        content = _rewrite_markdown_links(content, source)
        destination_path = output / destination
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if not content.startswith("---\n"):
            content = f"---\nlayout: default\n---\n\n{content}"
        destination_path.write_text(content, encoding="utf-8")

    for source, destination in SUPPORTING_FILES.items():
        destination_path = output / destination
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY_ROOT / source, destination_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / ".pages-source",
        help="staging directory inside the repository",
    )
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
