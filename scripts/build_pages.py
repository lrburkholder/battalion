"""Stage the explicitly approved documentation for GitHub Pages."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Keep this list explicit. Adding a document to the repository must not make it
# public automatically.
PUBLISHED_DOCUMENTS = {
    "README.md": "index.md",
    "spec.md": "spec.md",
    "plan.md": "plan.md",
    "docs/adrs/README.md": "docs/adrs/index.md",
    **{
        f"docs/adrs/adr{number:04d}.md": f"docs/adrs/adr{number:04d}.md"
        for number in range(1, 21)
    },
    "docs/rfcs/rfc0004.md": "docs/rfcs/rfc0004.md",
}

SUPPORTING_FILES = {
    "LICENSE": "LICENSE",
    ".github/pages/_config.yml": "_config.yml",
}

MARKDOWN_LINK = re.compile(r"(?P<prefix>\[[^]]+\]\()(?P<target>[^)#]+\.md)(?P<suffix>(?:#[^)]+)?\))")


def _rewrite_markdown_links(content: str, source: str) -> str:
    """Point links at the HTML paths produced by Jekyll."""

    source_directory = Path(source).parent

    def replace(match: re.Match[str]) -> str:
        target = (source_directory / match.group("target")).as_posix()
        destination = PUBLISHED_DOCUMENTS.get(target)
        if destination is None:
            return match.group(0)
        html_target = str(Path(destination).with_suffix(".html")).replace("\\", "/")
        current_destination = Path(PUBLISHED_DOCUMENTS[source]).parent
        relative_target = Path(html_target).relative_to(current_destination).as_posix()
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
        destination_path.write_text(
            f"---\nlayout: default\n---\n\n{content}", encoding="utf-8"
        )

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
