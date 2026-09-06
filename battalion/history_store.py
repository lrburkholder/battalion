"""SQLite adapter for disposable history indexes (ADR-0040)."""

from __future__ import annotations

from contextlib import closing, contextmanager
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from battalion.history import FILTER_FIELDS, HistoryQuery, utc_timestamp


PROJECTION_VERSION = 2


class ProjectionError(ValueError):
    """A projection cannot be read or safely replaced."""


class ProjectionReplacementRequired(ProjectionError):
    """An operator must explicitly authorize replacing unrecognized bytes."""


class HistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.receipt = path.with_suffix(".sha256")

    @contextmanager
    def locked(self):
        """Serialize Battalion access; a crash leaves an explicit stale lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        try:
            stream = lock.open("x")
        except FileExistsError as exc:
            raise ProjectionError(
                f"History projection is busy or has a stale lock: {lock}. "
                "Remove the lock only after confirming no history operation is active."
            ) from exc
        try:
            with stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            lock.unlink()

    def refresh(self, rows: list[dict], fingerprint: str, *, replace: bool = False) -> None:
        """Only replace recognized output automatically; interrupted publication fails safe."""
        if self.path.exists():
            recognized = (
                self.receipt.is_file()
                and self.receipt.read_bytes().strip()
                == sha256(self.path.read_bytes()).hexdigest().encode("ascii")
                and not any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-journal"))
            )
            if not recognized and not replace:
                raise ProjectionReplacementRequired(
                    "History projection appears externally modified or has no valid receipt. "
                    "Inspect/back up the projection, then explicitly rebuild with replacement."
                )
            if recognized:
                try:
                    with closing(self._connect()) as connection:
                        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                        healthy = connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
                    if not healthy:
                        raise sqlite3.DatabaseError("integrity check failed")
                except sqlite3.DatabaseError as exc:
                    if not replace:
                        raise ProjectionReplacementRequired(
                            "History projection is unreadable; explicit rebuild replacement required."
                        ) from exc
                else:
                    if metadata == {"version": str(PROJECTION_VERSION), "source": fingerprint} and not replace:
                        return
        if any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise ProjectionError("Close external SQLite writers and checkpoint their journal before rebuilding.")
        self._publish(rows, fingerprint)

    def _connect(self):
        # Read-only URI mode prevents queries from creating an empty database.
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)

    def _publish(self, rows: list[dict], fingerprint: str) -> None:
        with NamedTemporaryFile(dir=self.path.parent, suffix=".sqlite", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with closing(sqlite3.connect(temporary)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE evidence (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, text TEXT NOT NULL, started_at TEXT);
                    CREATE INDEX evidence_start ON evidence(started_at);
                    CREATE TABLE facets (evidence_id INTEGER NOT NULL, field TEXT NOT NULL, value TEXT);
                    CREATE INDEX facet_lookup ON facets(field, value, evidence_id);
                    CREATE TABLE costs (evidence_id INTEGER NOT NULL, currency TEXT NOT NULL, source TEXT NOT NULL, total TEXT NOT NULL);
                    CREATE INDEX cost_lookup ON costs(currency, source, evidence_id);
                """)
                connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
                    ("version", str(PROJECTION_VERSION)), ("source", fingerprint),
                ])
                for identifier, row in enumerate(rows):
                    payload = {key: value for key, value in row.items() if key != "search_text"}
                    connection.execute("INSERT INTO evidence VALUES (?, ?, ?, ?)", (
                        identifier, json.dumps(payload, ensure_ascii=False), row["search_text"], row.get("started_at"),
                    ))
                    connection.executemany("INSERT INTO costs VALUES (?, ?, ?, ?)", [
                        (identifier, cost["currency"], cost["source"], cost["total"])
                        for cost in row.get("cost_totals", [])
                    ])
                    for name in FILTER_FIELDS:
                        value = row.get(name)
                        values = value if isinstance(value, list) else [value]
                        connection.executemany("INSERT INTO facets VALUES (?, ?, ?)", [
                            (identifier, name, item) for item in (values or [None])
                        ])
            # Close SQLite handles before replacing files (also required on Windows).
            digest = sha256(temporary.read_bytes()).hexdigest()
            os.replace(temporary, self.path)
            # A crash between the database and receipt writes requires confirmation,
            # never silently authorizes destruction of an unrecognized database.
            self.receipt.write_text(digest + "\n", encoding="ascii")
        finally:
            temporary.unlink(missing_ok=True)

    def search(self, query: HistoryQuery, *, paginate: bool = True) -> tuple[int, list[dict]]:
        clauses = []
        parameters = []
        if query.text:
            clauses.append("instr(e.text, ?) > 0")
            parameters.append(query.text.casefold())
        for name, value in query.filters.items():
            clauses.append("EXISTS (SELECT 1 FROM facets f WHERE f.evidence_id=e.id AND f.field=? AND f.value IS ?)")
            parameters.extend((name, value))
        for value, operator in ((query.date_from, ">="), (query.date_to, "<=")):
            if value is not None:
                clauses.append(f"e.started_at {operator} ?")
                parameters.append(utc_timestamp(value))
        if query.cost_currency is not None:
            clauses.append("EXISTS (SELECT 1 FROM costs c WHERE c.evidence_id=e.id AND c.currency=? AND c.source=? AND decimal_range(c.total, ?, ?))")
            parameters.extend((query.cost_currency, query.cost_source,
                               str(query.cost_min) if query.cost_min is not None else None,
                               str(query.cost_max) if query.cost_max is not None else None))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        connection = self._connect()
        try:
            connection.create_function("decimal_range", 3, _decimal_range, deterministic=True)
            count = connection.execute("SELECT count(*) FROM evidence e" + where, parameters).fetchone()[0]
            sql = "SELECT payload FROM evidence e" + where + " ORDER BY e.id"
            if paginate:
                sql += " LIMIT ? OFFSET ?"
                parameters.extend((query.limit, query.offset))
            return count, [json.loads(row[0]) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()


def _decimal_range(total: str, minimum: str | None, maximum: str | None) -> bool:
    """Compare canonical decimal strings without SQLite REAL rounding."""
    amount = Decimal(total)
    return ((minimum is None or amount >= Decimal(minimum))
            and (maximum is None or amount <= Decimal(maximum)))
