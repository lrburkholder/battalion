"""Bounded source identities and comparison policy without filesystem IO."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from battalion.artifact_targets import Digest, normalize_target_path


class ProjectSourceUnavailable(ValueError):
    """The selected source cannot be fingerprinted safely and completely."""


class ProjectSourceChanged(ValueError):
    """Current source has changes not explained by verified scoped writes."""


class SourceFileFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    path: str = Field(max_length=1000)
    resolved_path: str = Field(max_length=1000)
    sha256: Digest
    symlink: bool
    executable: bool

    _paths = field_validator("path", "resolved_path")(normalize_target_path)


class ProjectSourceSnapshot(BaseModel):
    """Git-visible worktree contents, not a promise of Git reconstructability.

    Ignored untracked files and root Battalion metadata are outside this policy.
    Tracked files remain included even if an ignore rule matches them. The
    project root's absolute location never enters the identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal["1.0"] = "1.0"
    policy_id: Literal["git-visible-source-v1"] = "git-visible-source-v1"
    project_id: UUID
    head_revision: str = Field(pattern=r"^(?:unborn|[0-9a-f]{40}|[0-9a-f]{64})$")
    files: tuple[SourceFileFingerprint, ...] = Field(default=(), max_length=10000)
    revision: str = ""

    @field_validator("files")
    @classmethod
    def order_files(cls, files: tuple[SourceFileFingerprint, ...]) -> tuple[SourceFileFingerprint, ...]:
        if len({item.path for item in files}) != len(files):
            raise ValueError("source snapshot paths must be unique")
        return tuple(sorted(files, key=lambda item: item.path))

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        raw = json.dumps(self.model_dump(mode="json", exclude={"revision"}),
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected = "workspace-sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if self.revision and self.revision != expected:
            raise ValueError("source revision does not match its snapshot")
        object.__setattr__(self, "revision", expected)
        return self


def revalidate_project_source(
    baseline: ProjectSourceSnapshot,
    observed: ProjectSourceSnapshot,
    *,
    verified_write_digests: Mapping[str, str],
) -> str:
    """Keep the pinned baseline only when every change has exact write evidence.

    A declared write scope or target path is not evidence of a performed write.
    Deletions and new links/executable files lack attestations in the v1 scoped
    write tools and therefore require clarification, never automatic exclusion.
    """
    if (baseline.project_id, baseline.head_revision, baseline.policy_id) != (
        observed.project_id, observed.head_revision, observed.policy_id
    ):
        raise ProjectSourceChanged("Project, HEAD, or source policy changed; source evidence requires clarification.")
    before = {item.path: item for item in baseline.files}
    after = {item.path: item for item in observed.files}
    observed_outputs = {item.resolved_path: item.sha256 for item in observed.files}
    for path, digest in verified_write_digests.items():
        if observed_outputs.get(path) != digest:
            raise ProjectSourceChanged(f"Verified scoped output is missing or changed: {path}")
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if new is None or verified_write_digests.get(new.resolved_path) != new.sha256:
            raise ProjectSourceChanged(f"Source changed without matching scoped-write evidence: {path}")
        if old is None:
            if new.symlink or new.resolved_path != new.path or new.executable:
                raise ProjectSourceChanged(f"New source path has unverified link or executable metadata: {path}")
        elif (old.resolved_path, old.symlink, old.executable) != (
            new.resolved_path, new.symlink, new.executable
        ):
            raise ProjectSourceChanged(f"Source path metadata changed outside a scoped write: {path}")
    return baseline.revision
