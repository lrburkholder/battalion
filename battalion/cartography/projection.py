"""Non-authoritative Markdown projections and their independent freshness."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from battalion.cartography.models import Freshness, MapRevision


MARKDOWN_PROJECTION = Path(".battalion/cartography/map.md")
PROJECTION_STATUS = Path(".battalion/cartography/projection.json")


class ProjectionStatusError(ValueError):
    """A durable projection-status record cannot be trusted."""


class ProjectionStatus(BaseModel):
    """Derivative status; it never grants authority over canonical map state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    map_revision_id: str = Field(min_length=1, max_length=200)
    freshness: Freshness
    updated_at: datetime
    failure_kind: str | None = Field(default=None, max_length=100)


class MarkdownProjector:
    """Writes human-readable map projections without mutating canonical JSON."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._now = now

    @property
    def markdown_path(self) -> Path:
        return self.project_root / MARKDOWN_PROJECTION

    @property
    def status_path(self) -> Path:
        return self.project_root / PROJECTION_STATUS

    def publish(self, revision: MapRevision) -> ProjectionStatus:
        """Atomically write the derivative Markdown, then mark it current."""

        _atomic_write(self.markdown_path, render_markdown(revision))
        status = ProjectionStatus(
            map_revision_id=revision.map_revision_id,
            freshness=Freshness.CURRENT,
            updated_at=self._now(),
        )
        self._write_status(status)
        return status

    def mark_stale(self, revision: MapRevision) -> ProjectionStatus:
        """Record that canonical publication succeeded but its projection did not."""

        status = ProjectionStatus(
            map_revision_id=revision.map_revision_id,
            freshness=Freshness.STALE,
            updated_at=self._now(),
            failure_kind="generation_failed",
        )
        self._write_status(status)
        return status

    def status_for(self, revision: MapRevision) -> ProjectionStatus:
        """Return status for a revision; an older projection is necessarily stale."""

        if not self.status_path.is_file():
            return ProjectionStatus(
                map_revision_id=revision.map_revision_id,
                freshness=Freshness.UNKNOWN,
                updated_at=revision.created_at,
            )
        try:
            status = ProjectionStatus.model_validate_json(
                self.status_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ProjectionStatusError(
                f"Malformed Cartography projection status at {self.status_path}: {exc}"
            ) from exc
        if status.map_revision_id != revision.map_revision_id:
            return status.model_copy(update={"freshness": Freshness.STALE})
        return status

    def _write_status(self, status: ProjectionStatus) -> None:
        _atomic_write(self.status_path, status.model_dump_json(indent=2) + "\n")


def render_markdown(revision: MapRevision) -> str:
    """Render a deterministic, explicitly non-authoritative map overview."""

    lines = [
        "# Cartography map (generated)",
        "",
        "> This Markdown is a derivative projection. Canonical Cartography JSON owns map state.",
        "",
        f"- Map revision: `{revision.map_revision_id}`",
        f"- Repository revision: `{revision.repository_revision}`",
        "",
        "## Domains",
        "",
    ]
    for domain in sorted(revision.domains, key=lambda item: item.domain_id):
        lines.extend((f"### {domain.domain_id}", "", domain.purpose, ""))
    if not revision.domains:
        lines.extend(("No mapped domains.", ""))
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
