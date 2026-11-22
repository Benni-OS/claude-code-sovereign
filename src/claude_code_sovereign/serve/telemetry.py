"""Rolling P50/P90/P99 hook latency, blocked attack count, and USD saved telemetry."""

from __future__ import annotations

import math
from typing import Any

from ..core.trace_ledger import TraceLedger


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


class SovereignTelemetry:
    """Computes P50/P90/P99 hook latency, blocked attack counts, and estimated USD saved."""

    def __init__(self, ledger: TraceLedger, avg_runaway_cost_usd: float = 0.45) -> None:
        self.ledger = ledger
        self.avg_runaway_cost_usd = float(avg_runaway_cost_usd)

    def snapshot(self, session_id: str | None = None, limit: int = 5000) -> dict[str, Any]:
        events = self.ledger.list_events(session_id=session_id, limit=limit)
        latencies = sorted(e.latency_ms for e in events if e.latency_ms >= 0.0)
        blocked = sum(1 for e in events if e.decision in {"deny", "block"})
        allowed = sum(1 for e in events if e.decision == "allow")
        total_cost = sum(e.cost_usd for e in events)
        by_tool: dict[str, int] = {}
        for e in events:
            if e.tool_name:
                by_tool[e.tool_name] = by_tool.get(e.tool_name, 0) + 1

        return {
            "total_events": len(events),
            "allowed_events": allowed,
            "blocked_events": blocked,
            "estimated_usd_saved": round(blocked * self.avg_runaway_cost_usd, 4),
            "recorded_usd_spent": round(total_cost, 6),
            "latency_ms": {
                "p50": round(_percentile(latencies, 50), 4),
                "p90": round(_percentile(latencies, 90), 4),
                "p99": round(_percentile(latencies, 99), 4),
                "max": round(max(latencies), 4) if latencies else 0.0,
            },
            "tool_breakdown": by_tool,
        }
