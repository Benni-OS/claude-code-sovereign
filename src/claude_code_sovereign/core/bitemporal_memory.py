"""Cross-session bi-temporal knowledge graph surviving /compact and /clear."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class MemoryFact:
    """Bi-temporal architectural fact with valid-time and transaction-time bounds."""

    fact_id: str
    subject: str
    predicate: str
    obj: str
    valid_from: str
    valid_until: str | None
    recorded_at: str
    session_id: str
    confidence: float = 1.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.obj,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "recorded_at": self.recorded_at,
            "session_id": self.session_id,
            "confidence": self.confidence,
            "tags": self.tags,
        }


class BiTemporalMemory:
    """SQLite3 bi-temporal knowledge graph for architectural decisions & invariants."""

    def __init__(self, db_path: str | Path = ".sovereign/sovereign.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bitemporal_facts (
                    fact_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    obj TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    recorded_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    tags_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bt_subj_pred ON bitemporal_facts(subject, predicate)"
            )

    def record(
        self,
        subject: str,
        predicate: str,
        obj: str,
        session_id: str = "global",
        valid_from: str | None = None,
        valid_until: str | None = None,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> MemoryFact:
        now = datetime.now(timezone.utc).isoformat()
        v_from = valid_from or now
        fact_id = f"fact_{uuid.uuid4().hex[:12]}"
        tag_list = list(tags or [])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bitemporal_facts (
                    fact_id, subject, predicate, obj, valid_from, valid_until,
                    recorded_at, session_id, confidence, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_id,
                    subject,
                    predicate,
                    obj,
                    v_from,
                    valid_until,
                    now,
                    session_id,
                    float(confidence),
                    json.dumps(tag_list),
                ),
            )

        return MemoryFact(
            fact_id=fact_id,
            subject=subject,
            predicate=predicate,
            obj=obj,
            valid_from=v_from,
            valid_until=valid_until,
            recorded_at=now,
            session_id=session_id,
            confidence=float(confidence),
            tags=tag_list,
        )

    def invalidate(self, subject: str, predicate: str | None = None, valid_until: str | None = None) -> int:
        """Close valid_until on active facts without erasing historical audit trail."""
        v_until = valid_until or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if predicate:
                cur = conn.execute(
                    """
                    UPDATE bitemporal_facts
                    SET valid_until = ?
                    WHERE subject = ? AND predicate = ? AND valid_until IS NULL
                    """,
                    (v_until, subject, predicate),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE bitemporal_facts
                    SET valid_until = ?
                    WHERE subject = ? AND valid_until IS NULL
                    """,
                    (v_until, subject),
                )
            return cur.rowcount

    def query(
        self,
        subject: str | None = None,
        predicate: str | None = None,
        as_of_valid: str | None = None,
        as_of_recorded: str | None = None,
        limit: int = 50,
    ) -> list[MemoryFact]:
        clauses = ["1=1"]
        params: list[Any] = []

        if subject:
            clauses.append("subject LIKE ?")
            params.append(f"%{subject}%")
        if predicate:
            clauses.append("predicate LIKE ?")
            params.append(f"%{predicate}%")
        if as_of_valid:
            clauses.append("valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)")
            params.extend([as_of_valid, as_of_valid])
        else:
            clauses.append("valid_until IS NULL")

        if as_of_recorded:
            clauses.append("recorded_at <= ?")
            params.append(as_of_recorded)

        sql = f"""
            SELECT fact_id, subject, predicate, obj, valid_from, valid_until,
                   recorded_at, session_id, confidence, tags_json
            FROM bitemporal_facts
            WHERE {' AND '.join(clauses)}
            ORDER BY recorded_at DESC
            LIMIT ?
        """
        params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            MemoryFact(
                fact_id=str(r[0]),
                subject=str(r[1]),
                predicate=str(r[2]),
                obj=str(r[3]),
                valid_from=str(r[4]),
                valid_until=str(r[5]) if r[5] is not None else None,
                recorded_at=str(r[6]),
                session_id=str(r[7]),
                confidence=float(r[8]),
                tags=json.loads(r[9]),
            )
            for r in rows
        ]

    def export_compact_context(self, max_facts: int = 15) -> str:
        """Render active architectural invariants for UserPromptSubmit injection after /compact."""
        facts = self.query(limit=max_facts)
        if not facts:
            return ""
        lines = ["[Sovereign Bi-Temporal Memory — Active Invariants]"]
        for f in facts:
            lines.append(f"- ({f.subject}) --[{f.predicate}]--> {f.obj}")
        return "\n".join(lines)
