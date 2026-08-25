"""Disposable candidate adapters used only by the BTN-86 comparison harness."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from benchmarks.persistence.contract import MapEdge, MapFixture, MapRecord


def _from_dict(payload: dict) -> MapFixture:
    return MapFixture(
        fixture_id=payload["fixture_id"], revision_id=payload["revision_id"],
        repository_revision=payload["repository_revision"],
        records=tuple(MapRecord(**record) for record in payload["records"]),
        edges=tuple(MapEdge(**edge) for edge in payload["edges"]),
    )


def _retrieve(records: list[dict]) -> tuple[str, ...]:
    """The bounded application-context lookup shared by the control case."""
    by_id = {record["record_id"]: record for record in records}
    return (by_id["domain:application"]["record_id"], by_id["symbol:application.run"]["record_id"])


def _traverse(edges: list[dict]) -> tuple[str, ...]:
    outgoing = {edge["source_id"]: edge["target_id"] for edge in edges}
    symbol = outgoing["domain:application"]
    return (symbol, outgoing[symbol])


def _diff_records(before: list[dict], after: list[dict]) -> dict[str, tuple[str, ...]]:
    original = {record["record_id"]: record for record in before}
    changed = tuple(record["record_id"] for record in after if original[record["record_id"]] != record)
    return {"changed": changed, "unchanged": ("constraint:application-boundary",)}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class PersistenceCandidate(Protocol):
    name: str
    produces_projection: bool

    def publish(self, root: Path, fixture: MapFixture, *, fail: bool = False) -> None: ...
    def load(self, root: Path) -> MapFixture: ...
    def retrieve(self, root: Path) -> tuple[str, ...]: ...
    def traverse(self, root: Path) -> tuple[str, ...]: ...
    def diff(self, before: Path, after: Path) -> dict[str, tuple[str, ...]]: ...


@dataclass(frozen=True)
class IntegrationTemplate:
    """Facts to collect; values are evidence, not a pre-selection score."""

    candidate: str
    runtime_dependencies: tuple[str, ...]
    production_interfaces_changed: tuple[str, ...]
    raw_store_access_exposed: bool
    adapter_operations: tuple[str, ...]
    candidate_specific_files: tuple[str, ...]


@dataclass(frozen=True)
class MapChangeSummary:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    deleted: tuple[str, ...]
    preserved_authored: tuple[str, ...]


class _JsonCandidate:
    produces_projection = False
    filename = "map.json"

    def publish(self, root: Path, fixture: MapFixture, *, fail: bool = False) -> None:
        if not fail:
            _atomic_write(root / self.filename, json.dumps(asdict(fixture), indent=2, sort_keys=True))

    def load(self, root: Path) -> MapFixture:
        return _from_dict(self._payload(root))

    def _payload(self, root: Path) -> dict:
        return json.loads((root / self.filename).read_text(encoding="utf-8"))

    def retrieve(self, root: Path) -> tuple[str, ...]:
        return _retrieve(self._payload(root)["records"])

    def traverse(self, root: Path) -> tuple[str, ...]:
        return _traverse(self._payload(root)["edges"])

    def diff(self, before: Path, after: Path) -> dict[str, tuple[str, ...]]:
        return _diff_records(self._payload(before)["records"], self._payload(after)["records"])


class StructuredFilesCandidate(_JsonCandidate):
    name = "structured-files"


class MarkdownCharterCandidate:
    name = "markdown-charters"
    produces_projection = True
    filename = "charters.md"

    def publish(self, root: Path, fixture: MapFixture, *, fail: bool = False) -> None:
        if fail:
            return
        content = "# Cartography charters\n\n"
        for record in fixture.records:
            content += f"## {record.record_id}\n<!-- record: {json.dumps(asdict(record), sort_keys=True)} -->\n\n"
        content += "## Relationships\n<!-- edges: " + json.dumps([asdict(edge) for edge in fixture.edges], sort_keys=True) + " -->\n"
        metadata = {"fixture_id": fixture.fixture_id, "revision_id": fixture.revision_id, "repository_revision": fixture.repository_revision}
        _atomic_write(root / self.filename, content + f"<!-- revision: {json.dumps(metadata, sort_keys=True)} -->\n")

    def load(self, root: Path) -> MapFixture:
        payload = self._payload(root)
        return _from_dict(payload)

    def _payload(self, root: Path) -> dict:
        text = (root / self.filename).read_text(encoding="utf-8")
        records = [json.loads(value) for value in re.findall(r"<!-- record: (.*?) -->", text)]
        edges = json.loads(re.search(r"<!-- edges: (.*?) -->", text).group(1))
        metadata = json.loads(re.search(r"<!-- revision: (.*?) -->", text).group(1))
        return {**metadata, "records": records, "edges": edges}

    def retrieve(self, root: Path) -> tuple[str, ...]:
        return _retrieve(self._payload(root)["records"])

    def traverse(self, root: Path) -> tuple[str, ...]:
        return _traverse(self._payload(root)["edges"])

    def diff(self, before: Path, after: Path) -> dict[str, tuple[str, ...]]:
        return _diff_records(self._payload(before)["records"], self._payload(after)["records"])


class SqliteCandidate:
    name = "sqlite"
    produces_projection = False
    filename = "map.sqlite"

    def publish(self, root: Path, fixture: MapFixture, *, fail: bool = False) -> None:
        if fail:
            return
        root.mkdir(parents=True, exist_ok=True)
        staged = root / f".{self.filename}.staged"
        if staged.exists():
            staged.unlink()
        database = sqlite3.connect(staged)
        try:
            database.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            database.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            database.execute("CREATE TABLE edges (source_id TEXT, target_id TEXT, kind TEXT)")
            database.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                (("fixture_id", fixture.fixture_id), ("revision_id", fixture.revision_id),
                 ("repository_revision", fixture.repository_revision)),
            )
            database.executemany(
                "INSERT INTO records VALUES (?, ?)",
                ((record.record_id, json.dumps(asdict(record), sort_keys=True)) for record in fixture.records),
            )
            database.executemany(
                "INSERT INTO edges VALUES (?, ?, ?)",
                ((edge.source_id, edge.target_id, edge.kind) for edge in fixture.edges),
            )
            database.commit()
        finally:
            database.close()
        os.replace(staged, root / self.filename)

    def load(self, root: Path) -> MapFixture:
        database = sqlite3.connect(root / self.filename)
        try:
            metadata = dict(database.execute("SELECT key, value FROM metadata"))
            records = [json.loads(row[0]) for row in database.execute("SELECT payload FROM records ORDER BY record_id")]
            edges = [
                {"source_id": row[0], "target_id": row[1], "kind": row[2]}
                for row in database.execute("SELECT source_id, target_id, kind FROM edges ORDER BY rowid")
            ]
        finally:
            database.close()
        return _from_dict({**metadata, "records": records, "edges": edges})

    def retrieve(self, root: Path) -> tuple[str, ...]:
        database = sqlite3.connect(root / self.filename)
        try:
            rows = database.execute(
                "SELECT record_id FROM records WHERE record_id IN (?, ?) "
                "ORDER BY CASE record_id WHEN ? THEN 0 ELSE 1 END",
                ("domain:application", "symbol:application.run", "domain:application"),
            ).fetchall()
        finally:
            database.close()
        return tuple(row[0] for row in rows)

    def traverse(self, root: Path) -> tuple[str, ...]:
        database = sqlite3.connect(root / self.filename)
        try:
            symbol = database.execute(
                "SELECT target_id FROM edges WHERE source_id = ? ORDER BY rowid LIMIT 1",
                ("domain:application",),
            ).fetchone()[0]
            resource = database.execute(
                "SELECT target_id FROM edges WHERE source_id = ? ORDER BY rowid LIMIT 1",
                (symbol,),
            ).fetchone()[0]
        finally:
            database.close()
        return (symbol, resource)

    def diff(self, before: Path, after: Path) -> dict[str, tuple[str, ...]]:
        def records(root: Path) -> dict[str, str]:
            database = sqlite3.connect(root / self.filename)
            try:
                return dict(database.execute("SELECT record_id, payload FROM records"))
            finally:
                database.close()

        baseline, updated = records(before), records(after)
        changed = tuple(record_id for record_id in sorted(updated) if baseline[record_id] != updated[record_id])
        return {"changed": changed, "unchanged": ("constraint:application-boundary",)}


class GraphAdjacencyCandidate:
    """Portable embedded graph-oriented representation, not a graph service."""

    name = "graph-adjacency-json"
    filename = "map.graph.json"
    produces_projection = False

    def publish(self, root: Path, fixture: MapFixture, *, fail: bool = False) -> None:
        if fail:
            return
        nodes = {record.record_id: asdict(record) for record in fixture.records}
        adjacency: dict[str, list[dict[str, str]]] = {}
        for edge in fixture.edges:
            adjacency.setdefault(edge.source_id, []).append({
                "target_id": edge.target_id, "kind": edge.kind,
            })
        _atomic_write(root / self.filename, json.dumps({
            "fixture_id": fixture.fixture_id,
            "revision_id": fixture.revision_id,
            "repository_revision": fixture.repository_revision,
            "nodes": nodes,
            "adjacency": adjacency,
        }, indent=2, sort_keys=True))

    def load(self, root: Path) -> MapFixture:
        return _from_dict(self._flat_payload(root))

    def _payload(self, root: Path) -> dict:
        return json.loads((root / self.filename).read_text(encoding="utf-8"))

    def _flat_payload(self, root: Path) -> dict:
        payload = self._payload(root)
        edges = [
            {"source_id": source_id, "target_id": edge["target_id"], "kind": edge["kind"]}
            for source_id, outgoing in payload["adjacency"].items()
            for edge in outgoing
        ]
        return {
            "fixture_id": payload["fixture_id"],
            "revision_id": payload["revision_id"],
            "repository_revision": payload["repository_revision"],
            "records": list(payload["nodes"].values()),
            "edges": edges,
        }

    def retrieve(self, root: Path) -> tuple[str, ...]:
        payload = self._payload(root)
        return (payload["nodes"]["domain:application"]["record_id"], payload["nodes"]["symbol:application.run"]["record_id"])

    def traverse(self, root: Path) -> tuple[str, ...]:
        adjacency = self._payload(root)["adjacency"]
        symbol = adjacency["domain:application"][0]["target_id"]
        return (symbol, adjacency[symbol][0]["target_id"])

    def diff(self, before: Path, after: Path) -> dict[str, tuple[str, ...]]:
        baseline, updated = self._payload(before)["nodes"], self._payload(after)["nodes"]
        changed = tuple(record_id for record_id in sorted(updated) if baseline[record_id] != updated[record_id])
        return {"changed": changed, "unchanged": ("constraint:application-boundary",)}


class HybridSqliteMarkdownCandidate(SqliteCandidate):
    name = "hybrid-sqlite-markdown"
    produces_projection = True

    def publish(self, root: Path, fixture: MapFixture, *, fail: bool = False) -> None:
        super().publish(root, fixture, fail=fail)
        if not fail:
            projection = "# Generated map projection\n\n" + "\n".join(f"- {record.record_id}" for record in fixture.records) + "\n"
            _atomic_write(root / "charter-projection.md", projection)


class GraphMarkdownProjectionCandidate(GraphAdjacencyCandidate):
    """Structured canonical graph with reviewable, explicitly generated charters."""

    name = "graph-adjacency-markdown"
    produces_projection = True

    def _manifest_path(self, root: Path) -> Path:
        return root / "projection-status.json"

    def _render_projection(self, fixture: MapFixture) -> str:
        groups = {
            "Domains": [record for record in fixture.records if record.kind == "domain"],
            "Symbols": [record for record in fixture.records if record.kind == "symbol"],
            "Resources": [record for record in fixture.records if record.kind == "resource"],
            "Path bindings": [record for record in fixture.records if record.kind == "path-binding"],
            "Constraints": [record for record in fixture.records if record.kind == "constraint"],
        }
        lines = [
            "# Cartography map", "",
            "> Generated projection - edit the canonical graph representation, not this file.", "",
            f"Mapped revision: `{fixture.repository_revision}`", "",
        ]
        for heading, records in groups.items():
            lines.extend([f"## {heading}", "", "| ID | Summary |", "| --- | --- |"])
            for record in records:
                if record.kind == "symbol":
                    summary = f"`{record.data['path']}` :: `{record.data['locator']}`"
                elif record.kind == "resource":
                    summary = f"{record.data['kind']}: `{record.data['locator']}`"
                elif record.kind == "path-binding":
                    summary = f"`{record.data['pattern']}` -> " + ", ".join(f"`{target}`" for target in record.data["targets"])
                else:
                    summary = record.data.get("purpose") or record.data.get("statement") or record.data.get("locator", "")
                lines.append(f"| `{record.record_id}` | {summary} |")
            lines.append("")
        lines.extend(["## Relationships", "", "| Source | Kind | Target |", "| --- | --- | --- |"])
        lines.extend(f"| `{edge.source_id}` | {edge.kind} | `{edge.target_id}` |" for edge in fixture.edges)
        return "\n".join(lines) + "\n"

    def publish(
        self,
        root: Path,
        fixture: MapFixture,
        *,
        fail: bool = False,
        fail_projection: bool = False,
    ) -> None:
        if fail:
            return
        # A pending manifest is written before the canonical graph. A crash at
        # any point is therefore visible to readers rather than mislabeling an
        # old projection as a view of the new revision.
        _atomic_write(self._manifest_path(root), json.dumps({
            "state": "pending", "revision_id": fixture.revision_id,
        }, sort_keys=True))
        super().publish(root, fixture)
        if fail_projection:
            return
        _atomic_write(root / "charter-projection.md", self._render_projection(fixture))
        _atomic_write(self._manifest_path(root), json.dumps({
            "state": "ready", "revision_id": fixture.revision_id,
        }, sort_keys=True))

    def projection_status(self, root: Path) -> str:
        manifest = json.loads(self._manifest_path(root).read_text(encoding="utf-8"))
        canonical_revision = self._payload(root)["revision_id"]
        if manifest["state"] != "ready" or manifest["revision_id"] != canonical_revision:
            return "stale"
        return "ready"

    def path_lookup(self, root: Path, path: str) -> tuple[str, ...]:
        nodes = self._payload(root)["nodes"]
        matches = [
            node_id for node_id, record in nodes.items()
            if path == record["data"].get("path")
            or path in record["data"].get("paths", [])
            or path == record["data"].get("pattern")
        ]
        return tuple(sorted(matches))

    def change_summary(self, before: Path, after: Path) -> MapChangeSummary:
        baseline = self._payload(before)["nodes"]
        updated = self._payload(after)["nodes"]
        return MapChangeSummary(
            added=tuple(sorted(set(updated) - set(baseline))),
            changed=tuple(sorted(node_id for node_id in set(updated) & set(baseline) if updated[node_id] != baseline[node_id])),
            deleted=tuple(sorted(set(baseline) - set(updated))),
            preserved_authored=tuple(sorted(
                node_id for node_id in set(updated) & set(baseline)
                if updated[node_id]["authored"] and updated[node_id] == baseline[node_id]
            )),
        )


CANDIDATES: tuple[PersistenceCandidate, ...] = (
    MarkdownCharterCandidate(), StructuredFilesCandidate(), SqliteCandidate(),
    GraphAdjacencyCandidate(), HybridSqliteMarkdownCandidate(),
    GraphMarkdownProjectionCandidate(),
)


def integration_templates() -> tuple[IntegrationTemplate, ...]:
    operations = ("publish-complete-revision", "load-bounded-records", "diff-revisions", "traverse-neighbors")
    return tuple(IntegrationTemplate(
        candidate=item.name, runtime_dependencies=(), production_interfaces_changed=(),
        raw_store_access_exposed=False, adapter_operations=operations,
        candidate_specific_files=(
            (item.filename, "charter-projection.md", "projection-status.json")
            if item.name == "graph-adjacency-markdown" else (item.filename,)
        ),
    ) for item in CANDIDATES)
