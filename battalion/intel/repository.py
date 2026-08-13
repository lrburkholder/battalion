"""Immutable, local persistence for human-accepted Instincts."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from battalion.intel.models import AcceptedInstinct, InstinctId


_INSTINCT_ID_ADAPTER = TypeAdapter(InstinctId)


class ImmutableInstinctError(FileExistsError):
    """Raised when a caller attempts to replace an accepted Instinct."""


class InstinctNotFoundError(KeyError):
    """Raised when an Instinct identifier is absent from the repository."""


class IntelRepository:
    """A directory-backed, append-only collection of accepted Instincts.

    Each stable identifier owns one JSON file. Exclusive file creation makes an
    identifier immutable, while a newer record can point back to an existing
    record through ``supersedes_id``.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def store(self, instinct: AcceptedInstinct) -> None:
        """Persist a new accepted Instinct without replacing existing data."""

        if not isinstance(instinct, AcceptedInstinct):
            raise TypeError("the Intel repository stores accepted Instincts only")

        if instinct.supersedes_id is not None:
            self.get(instinct.supersedes_id)

        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(instinct.instinct_id)
        payload = instinct.model_dump_json(indent=2) + "\n"
        try:
            with path.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(payload)
        except FileExistsError as exc:
            raise ImmutableInstinctError(
                f"Instinct {instinct.instinct_id} already exists and is immutable"
            ) from exc

    def get(self, instinct_id: str) -> AcceptedInstinct:
        """Return an accepted Instinct, including superseded history."""

        path = self._path_for(instinct_id)
        if not path.is_file():
            raise InstinctNotFoundError(f"Instinct {instinct_id} was not found")
        return self._load(path, expected_id=instinct_id)

    def list_all(self) -> list[AcceptedInstinct]:
        """Return every accepted Instinct in stable identifier order."""

        if not self.root.exists():
            return []
        return [
            self._load(path, expected_id=path.stem)
            for path in sorted(self.root.glob("INS-*.json"), key=lambda item: item.name)
        ]

    def list_active(self) -> list[AcceptedInstinct]:
        """Return accepted Instincts that no newer record supersedes."""

        instincts = self.list_all()
        known_ids = {instinct.instinct_id for instinct in instincts}
        superseded_ids = {
            instinct.supersedes_id
            for instinct in instincts
            if instinct.supersedes_id is not None
        }
        missing_ids = superseded_ids - known_ids
        if missing_ids:
            missing = sorted(missing_ids)[0]
            raise InstinctNotFoundError(
                f"Superseded Instinct {missing} is absent from the repository"
            )
        return [
            instinct
            for instinct in instincts
            if instinct.instinct_id not in superseded_ids
        ]

    def _path_for(self, instinct_id: str) -> Path:
        validated_id = _INSTINCT_ID_ADAPTER.validate_python(instinct_id)
        return self.root / f"{validated_id}.json"

    @staticmethod
    def _load(path: Path, *, expected_id: str) -> AcceptedInstinct:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Instinct file at {path} is not valid JSON: {exc}") from exc

        instinct = AcceptedInstinct.model_validate(raw)
        if instinct.instinct_id != expected_id:
            raise ValueError(
                f"Instinct identifier {instinct.instinct_id} does not match "
                f"repository filename {expected_id}"
            )
        return instinct
