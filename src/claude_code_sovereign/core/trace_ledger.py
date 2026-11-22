"""Immutable SHA-256 chained trajectory ledger and $0.00 deterministic time-travel replay."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .firewall import SovereignFirewall

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class TraceEvent:
    """Single immutable event in the SHA-256 chained trajectory ledger."""

    seq: int
    timestamp: str
    session_id: str
    event_type: str
    tool_name: str
    payload: dict[str, Any]
    decision: str
    latency_ms: float
    cost_usd: float
    prev_hash: str
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "payload": self.payload,
            "decision": self.decision,
            "latency_ms": round(self.latency_ms, 4),
            "cost_usd": round(self.cost_usd, 6),
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }


def compute_event_hash(
    seq: int,
    timestamp: str,
    session_id: str,
    event_type: str,
    tool_name: str,
    payload: dict[str, Any],
    decision: str,
    prev_hash: str,
) -> str:
    """Compute canonical SHA-256 digest binding this event to the previous hash."""
    canonical = json.dumps(
        {
            "seq": seq,
            "timestamp": timestamp,
            "session_id": session_id,
            "event_type": event_type,
            "tool_name": tool_name,
            "payload": payload,
            "decision": decision,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TraceLedger:
    """Dual-persisted (.jsonl + SQLite3) SHA-256 trajectory ledger with offline replay."""

    def __init__(
        self,
        jsonl_path: str | Path = ".sovereign/trace.jsonl",
        db_path: str | Path = ".sovereign/sovereign.db",
    ) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.db_path = Path(db_path)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS trace_events (
                    seq INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    cost_usd REAL NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_session ON trace_events(session_id, seq)"
            )

    def _last_head(self, conn: sqlite3.Connection) -> tuple[int, str]:
        row = conn.execute(
            "SELECT seq, event_hash FROM trace_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row:
            return int(row[0]), str(row[1])
        return 0, GENESIS_HASH

    def append(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        tool_name: str = "",
        decision: str = "allow",
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
    ) -> TraceEvent:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            last_seq, prev_hash = self._last_head(conn)
            seq = last_seq + 1
            event_hash = compute_event_hash(
                seq=seq,
                timestamp=timestamp,
                session_id=session_id,
                event_type=event_type,
                tool_name=tool_name,
                payload=payload,
                decision=decision,
                prev_hash=prev_hash,
            )
            conn.execute(
                """
                INSERT INTO trace_events (
                    seq, timestamp, session_id, event_type, tool_name,
                    payload_json, decision, latency_ms, cost_usd, prev_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq,
                    timestamp,
                    session_id,
                    event_type,
                    tool_name,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                    decision,
                    float(latency_ms),
                    float(cost_usd),
                    prev_hash,
                    event_hash,
                ),
            )

        event = TraceEvent(
            seq=seq,
            timestamp=timestamp,
            session_id=session_id,
            event_type=event_type,
            tool_name=tool_name,
            payload=payload,
            decision=decision,
            latency_ms=float(latency_ms),
            cost_usd=float(cost_usd),
            prev_hash=prev_hash,
            event_hash=event_hash,
        )
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event

    def list_events(self, session_id: str | None = None, limit: int = 100) -> list[TraceEvent]:
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    """
                    SELECT seq, timestamp, session_id, event_type, tool_name, payload_json,
                           decision, latency_ms, cost_usd, prev_hash, event_hash
                    FROM trace_events
                    WHERE session_id = ?
                    ORDER BY seq ASC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT seq, timestamp, session_id, event_type, tool_name, payload_json,
                           decision, latency_ms, cost_usd, prev_hash, event_hash
                    FROM trace_events
                    ORDER BY seq ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        return [
            TraceEvent(
                seq=int(r[0]),
                timestamp=str(r[1]),
                session_id=str(r[2]),
                event_type=str(r[3]),
                tool_name=str(r[4]),
                payload=json.loads(r[5]),
                decision=str(r[6]),
                latency_ms=float(r[7]),
                cost_usd=float(r[8]),
                prev_hash=str(r[9]),
                event_hash=str(r[10]),
            )
            for r in rows
        ]

    def verify_chain(self) -> tuple[bool, int, str]:
        """Verify integrity of the entire SHA-256 chain from GENESIS_HASH."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT seq, timestamp, session_id, event_type, tool_name, payload_json,
                       decision, prev_hash, event_hash
                FROM trace_events
                ORDER BY seq ASC
                """
            ).fetchall()

        expected_prev = GENESIS_HASH
        for idx, r in enumerate(rows):
            seq = int(r[0])
            prev_hash = str(r[7])
            stored_hash = str(r[8])
            if prev_hash != expected_prev:
                return False, idx, f"Broken prev_hash link at seq={seq}"
            recomputed = compute_event_hash(
                seq=seq,
                timestamp=str(r[1]),
                session_id=str(r[2]),
                event_type=str(r[3]),
                tool_name=str(r[4]),
                payload=json.loads(r[5]),
                decision=str(r[6]),
                prev_hash=prev_hash,
            )
            if recomputed != stored_hash:
                return False, idx, f"Hash mismatch (tamper detected) at seq={seq}"
            expected_prev = stored_hash

        return True, len(rows), "SHA-256 trajectory chain verified intact"

    def replay(
        self,
        session_id: str,
        from_seq: int = 0,
        counterfactual_cap_usd: float | None = None,
        counterfactual_firewall: SovereignFirewall | None = None,
    ) -> dict[str, Any]:
        """Deterministic $0.00 offline time-travel replay & counterfactual fork diffing."""
        events = [e for e in self.list_events(session_id=session_id, limit=10_000) if e.seq >= from_seq]
        firewall = counterfactual_firewall or SovereignFirewall()
        cumulative_usd = 0.0
        diffs: list[dict[str, Any]] = []
        replayed_steps = 0

        for ev in events:
            replayed_steps += 1
            cumulative_usd += ev.cost_usd
            new_decision = ev.decision
            reason = "unchanged"

            if counterfactual_cap_usd is not None and cumulative_usd > counterfactual_cap_usd:
                new_decision = "deny"
                reason = f"Counterfactual budget cap ${counterfactual_cap_usd:.2f} exceeded (${cumulative_usd:.4f})"
            elif ev.tool_name and isinstance(ev.payload, dict):
                verdict = firewall.scan_tool_call(ev.tool_name, ev.payload)
                if not verdict.allowed:
                    new_decision = "deny"
                    reason = f"Counterfactual firewall rule {verdict.matched_rule}: {verdict.reason}"

            if new_decision != ev.decision:
                diffs.append(
                    {
                        "seq": ev.seq,
                        "tool_name": ev.tool_name,
                        "original_decision": ev.decision,
                        "counterfactual_decision": new_decision,
                        "reason": reason,
                    }
                )

        return {
            "session_id": session_id,
            "from_seq": from_seq,
            "replayed_events": replayed_steps,
            "replay_api_cost_usd": 0.0,
            "recorded_session_cost_usd": round(cumulative_usd, 6),
            "divergences": diffs,
            "divergence_count": len(diffs),
        }
