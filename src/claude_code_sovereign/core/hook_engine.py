"""Official Anthropic Claude Code Lifecycle Hook dispatcher."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .bitemporal_memory import BiTemporalMemory
from .budget_governor import BudgetGovernor
from .drift_lock import DriftLock
from .firewall import SovereignFirewall
from .trace_ledger import TraceLedger


class HookEngine:
    """Dispatches PreToolUse, PostToolUse, UserPromptSubmit, Stop, and SubagentStop hooks."""

    def __init__(
        self,
        workspace_root: str | Path = ".",
        budget_cap_usd: float = 5.00,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        sovereign_dir = self.workspace_root / ".sovereign"
        db_path = sovereign_dir / "sovereign.db"
        jsonl_path = sovereign_dir / "trace.jsonl"

        self.firewall = SovereignFirewall()
        self.budget = BudgetGovernor(db_path=db_path, default_cap_usd=budget_cap_usd)
        self.ledger = TraceLedger(jsonl_path=jsonl_path, db_path=db_path)
        self.drift = DriftLock()
        self.memory = BiTemporalMemory(db_path=db_path)

    def handle_event(self, event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a Claude Code hook JSON payload and return official hook response JSON."""
        t0 = time.perf_counter()
        session_id = str(payload.get("session_id") or "default")
        subagent_id = str(payload.get("subagent_id") or "main")

        if event_name == "PreToolUse":
            tool_name = str(payload.get("tool_name") or "")
            tool_input = payload.get("tool_input") or {}
            if not isinstance(tool_input, dict):
                tool_input = {"raw": str(tool_input)}

            budget_status = self.budget.check_budget(session_id)
            if not budget_status.allowed:
                latency_ms = (time.perf_counter() - t0) * 1000.0
                self.ledger.append(
                    session_id,
                    "PreToolUse",
                    tool_input,
                    tool_name=tool_name,
                    decision="deny",
                    latency_ms=latency_ms,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[Sovereign Budget Governor] Hard cap exceeded: "
                            f"${budget_status.spent_usd:.4f} / ${budget_status.cap_usd:.2f}"
                        ),
                    }
                }

            verdict = self.firewall.scan_tool_call(
                tool_name,
                tool_input,
                workspace_root=str(self.workspace_root),
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            decision = "allow" if verdict.allowed else "deny"
            self.ledger.append(
                session_id,
                "PreToolUse",
                tool_input,
                tool_name=tool_name,
                decision=decision,
                latency_ms=latency_ms,
            )

            if not verdict.allowed:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[Sovereign Firewall {verdict.matched_rule}] {verdict.reason}"
                        ),
                    }
                }

            out: dict[str, Any] = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Cleared by Sovereign Firewall & Budget Governor",
                }
            }
            if budget_status.state == "WARNING":
                out["hookSpecificOutput"]["additionalContext"] = (
                    f"[Sovereign Warning] Session budget at {budget_status.utilization_pct:.1f}% "
                    f"(${budget_status.spent_usd:.4f}/${budget_status.cap_usd:.2f})."
                )
            return out

        if event_name == "PostToolUse":
            tool_name = str(payload.get("tool_name") or "")
            usage = payload.get("usage") or {}
            in_tok = int(usage.get("input_tokens", 0)) if isinstance(usage, dict) else 0
            out_tok = int(usage.get("output_tokens", 0)) if isinstance(usage, dict) else 0
            model = str(payload.get("model") or "claude-sonnet-4")

            cost_usd = 0.0
            if in_tok > 0 or out_tok > 0:
                cost_usd = self.budget.estimate_cost(in_tok, out_tok, model)
                self.budget.record_usage(
                    session_id=session_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    model=model,
                    subagent_id=subagent_id,
                    cost_usd=cost_usd,
                )

            latency_ms = (time.perf_counter() - t0) * 1000.0
            self.ledger.append(
                session_id,
                "PostToolUse",
                {"tool_name": tool_name, "usage": usage},
                tool_name=tool_name,
                decision="allow",
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            )
            return {}

        if event_name == "UserPromptSubmit":
            prompt = str(payload.get("prompt") or "")
            verdict = self.firewall.scan_text(prompt, context="UserPromptSubmit")
            latency_ms = (time.perf_counter() - t0) * 1000.0
            decision = "allow" if verdict.allowed else "block"
            self.ledger.append(
                session_id,
                "UserPromptSubmit",
                {"prompt_len": len(prompt), "rule": verdict.matched_rule},
                decision=decision,
                latency_ms=latency_ms,
            )
            if not verdict.allowed:
                return {
                    "decision": "block",
                    "reason": f"[Sovereign Firewall {verdict.matched_rule}] {verdict.reason}",
                }

            mem_ctx = self.memory.export_compact_context()
            if mem_ctx:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": mem_ctx,
                    }
                }
            return {}

        if event_name in {"Stop", "SubagentStop"}:
            modified_files = payload.get("modified_files")
            drift_res = self.drift.verify_spec_contract(
                self.workspace_root,
                modified_files=modified_files if isinstance(modified_files, list) else None,
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            decision = "allow" if drift_res.passed else "block"
            self.ledger.append(
                session_id,
                event_name,
                drift_res.to_dict(),
                decision=decision,
                latency_ms=latency_ms,
            )
            if not drift_res.passed:
                return {
                    "decision": "block",
                    "reason": f"[Sovereign Reality Lock] {drift_res.summary}",
                }
            return {}

        return {}

    def dispatch_stdin(self, event_name: str) -> int:
        """Read JSON from sys.stdin, execute hook, and print JSON to sys.stdout."""
        raw = sys.stdin.read().strip()
        payload: dict[str, Any] = json.loads(raw) if raw else {}
        response = self.handle_event(event_name, payload)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        return 0
