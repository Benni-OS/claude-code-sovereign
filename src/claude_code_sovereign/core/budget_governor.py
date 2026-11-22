"""Per-session and per-subagent token/USD economic circuit breaker."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# USD per 1M tokens (input, output)
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.00, 75.00),
    "claude-opus-4-1": (15.00, 75.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-haiku-4": (0.80, 4.00),
    "default": (3.00, 15.00),
}


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot of session/subagent economic budget utilization."""

    session_id: str
    spent_usd: float
    cap_usd: float
    spent_tokens: int
    token_cap: int
    utilization_pct: float
    state: str  # "OK" | "WARNING" | "EXCEEDED"
    remaining_usd: float
    subagent_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.state != "EXCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "spent_usd": round(self.spent_usd, 6),
            "cap_usd": round(self.cap_usd, 6),
            "spent_tokens": self.spent_tokens,
            "token_cap": self.token_cap,
            "utilization_pct": round(self.utilization_pct, 2),
            "state": self.state,
            "remaining_usd": round(self.remaining_usd, 6),
            "allowed": self.allowed,
            "subagent_breakdown": {k: round(v, 6) for k, v in self.subagent_breakdown.items()},
        }


class BudgetGovernor:
    """SQLite-backed economic circuit breaker with 80% warning and 100% hard block."""

    def __init__(
        self,
        db_path: str | Path = ".sovereign/sovereign.db",
        default_cap_usd: float = 5.00,
        default_token_cap: int = 1_500_000,
        warning_ratio: float = 0.80,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_cap_usd = float(default_cap_usd)
        self.default_token_cap = int(default_token_cap)
        self.warning_ratio = float(warning_ratio)
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
                CREATE TABLE IF NOT EXISTS budget_caps (
                    session_id TEXT PRIMARY KEY,
                    cap_usd REAL NOT NULL,
                    token_cap INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    subagent_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def estimate_cost(input_tokens: int, output_tokens: int, model: str = "default") -> float:
        key = model.lower()
        rates = MODEL_PRICING_PER_1M.get("default")
        for prefix, pair in MODEL_PRICING_PER_1M.items():
            if prefix != "default" and prefix in key:
                rates = pair
                break
        in_rate, out_rate = rates  # type: ignore[misc]
        return (max(0, input_tokens) * in_rate + max(0, output_tokens) * out_rate) / 1_000_000.0

    def set_cap(self, session_id: str, cap_usd: float, token_cap: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        t_cap = self.default_token_cap if token_cap is None else int(token_cap)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO budget_caps (session_id, cap_usd, token_cap, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    cap_usd = excluded.cap_usd,
                    token_cap = excluded.token_cap,
                    updated_at = excluded.updated_at
                """,
                (session_id, float(cap_usd), t_cap, now),
            )

    def _get_caps(self, conn: sqlite3.Connection, session_id: str) -> tuple[float, int]:
        row = conn.execute(
            "SELECT cap_usd, token_cap FROM budget_caps WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row:
            return float(row[0]), int(row[1])
        return self.default_cap_usd, self.default_token_cap

    def record_usage(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "claude-sonnet-4",
        subagent_id: str = "main",
        cost_usd: float | None = None,
    ) -> BudgetStatus:
        computed_cost = (
            float(cost_usd)
            if cost_usd is not None
            else self.estimate_cost(input_tokens, output_tokens, model)
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO budget_ledger (
                    session_id, subagent_id, model, input_tokens, output_tokens, cost_usd, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, subagent_id, model, int(input_tokens), int(output_tokens), computed_cost, now),
            )
        return self.check_budget(session_id)

    def check_budget(self, session_id: str) -> BudgetStatus:
        with self._connect() as conn:
            cap_usd, token_cap = self._get_caps(conn, session_id)
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(cost_usd), 0.0),
                    COALESCE(SUM(input_tokens + output_tokens), 0)
                FROM budget_ledger
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            spent_usd = float(row[0]) if row else 0.0
            spent_tokens = int(row[1]) if row else 0

            sub_rows = conn.execute(
                """
                SELECT subagent_id, COALESCE(SUM(cost_usd), 0.0)
                FROM budget_ledger
                WHERE session_id = ?
                GROUP BY subagent_id
                """,
                (session_id,),
            ).fetchall()
            breakdown = {str(r[0]): float(r[1]) for r in sub_rows}

        usd_ratio = (spent_usd / cap_usd) if cap_usd > 0 else 1.0
        tok_ratio = (spent_tokens / token_cap) if token_cap > 0 else 1.0
        ratio = max(usd_ratio, tok_ratio)
        util_pct = ratio * 100.0

        if ratio >= 1.0:
            state = "EXCEEDED"
        elif ratio >= self.warning_ratio:
            state = "WARNING"
        else:
            state = "OK"

        return BudgetStatus(
            session_id=session_id,
            spent_usd=spent_usd,
            cap_usd=cap_usd,
            spent_tokens=spent_tokens,
            token_cap=token_cap,
            utilization_pct=util_pct,
            state=state,
            remaining_usd=max(0.0, cap_usd - spent_usd),
            subagent_breakdown=breakdown,
        )
