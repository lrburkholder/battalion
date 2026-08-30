"""Keep the public recovery routes and recorded reason vocabulary usable offline."""

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from battalion.cli import INTERRUPT_GUIDES
from battalion.state.models import ProgressStage, TestExecutionClassification as ExecutionClassification
from scripts.build_pages import PUBLISHED_DOCUMENTS, _rewrite_markdown_links


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/troubleshooting.md"


def anchors(text: str) -> set[str]:
    explicit = re.findall(r'<a id="([^"]+)"', text)
    headings = re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    slugs = [re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-") for title in headings]
    return set(explicit + slugs)


def test_recovery_links_resolve_in_repository_and_staged_publication():
    documents = [
        GUIDE, ROOT / "README.md", ROOT / "plan.md", ROOT / "docs/getting-started.md",
        ROOT / "docs/ui/workflow.md", ROOT / "docs/uat/cli.md", ROOT / "docs/uat/desktop.md",
    ]
    checked = 0
    for document in documents:
        source = document.relative_to(ROOT).as_posix()
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            link = urlsplit(target)
            if link.scheme or (document != GUIDE and "troubleshooting.md" not in link.path):
                continue
            path = (document.parent / unquote(link.path)).resolve() if link.path else document
            assert path.is_file(), (source, target)
            if link.fragment:
                assert unquote(link.fragment) in anchors(path.read_text(encoding="utf-8")), (source, target)
            relative = path.relative_to(ROOT).as_posix()
            assert relative in PUBLISHED_DOCUMENTS, (source, target)
            if source in PUBLISHED_DOCUMENTS and link.path:
                rewritten = _rewrite_markdown_links(f"[route]({target})", source)
                assert ".md" not in rewritten, rewritten
                if link.fragment:
                    assert "#" + link.fragment in rewritten
            checked += 1
    assert checked > 30


def test_all_cli_routes_and_reviewer_recovery_codes_have_published_explanations():
    text = GUIDE.read_text(encoding="utf-8")
    assert set(INTERRUPT_GUIDES.values()) | {"run-stopped", "resume-recovery"} <= anchors(text)
    assert len(re.findall(r'<a id="([^"]+)"', text)) == len(set(re.findall(r'<a id="([^"]+)"', text)))
    for code in [*ExecutionClassification, *ProgressStage]:
        assert f"`{code.value}`" in text, f"Undocumented evidence code: {code.value}"
    assert text.split("\n## ")[1].startswith("Collect diagnostics first\n")
