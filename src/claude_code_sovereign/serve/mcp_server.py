"""Zero-dependency JSON-RPC 2.0 stdio MCP server for Claude Code Sovereign."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..core.bitemporal_memory import BiTemporalMemory
from ..core.budget_governor import BudgetGovernor
from ..core.drift_lock import DriftLock
from ..core.firewall import SovereignFirewall
from ..core.trace_ledger import TraceLedger

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "sovereign_firewall_scan",
        "description": "Scan a tool call or prompt text for prompt injection, credential exfiltration, or destructive shell commands.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Optional tool name (e.g., Bash, Read, WebFetch)"},
                "tool_input": {"type": "object", "description": "Optional tool input payload"},
                "text": {"type": "string", "description": "Optional raw text to scan"},
            },
        },
    },
    {
        "name": "sovereign_budget_status",
        "description": "Query current session USD and token budget utilization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "default": "default"},
            },
        },
    },
    {
        "name": "sovereign_trace_inspect",
        "description": "Inspect immutable SHA-256 chained trajectory events and verify chain integrity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "sovereign_trace_replay",
        "description": "Run $0.00 deterministic offline time-travel replay & counterfactual fork diffing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "default": "default"},
                "from_seq": {"type": "integer", "default": 0},
                "counterfactual_cap_usd": {"type": "number"},
            },
        },
    },
    {
        "name": "sovereign_memory_query",
        "description": "Query the cross-session bi-temporal knowledge graph for active architectural invariants.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "as_of_valid": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "sovereign_memory_record",
        "description": "Record an architectural decision, constraint, or invariant into bi-temporal memory.",
        "inputSchema": {
            "type": "object",
            "required": ["subject", "predicate", "object"],
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
                "session_id": {"type": "string", "default": "global"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "sovereign_sdd_verify",
        "description": "Run AST syntax & Spec-Driven Reality Lock verification across modified files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "modified_files": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
]


class SovereignMCPServer:
    """Stdio JSON-RPC 2.0 MCP Server."""

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.root = Path(workspace_root).resolve()
        sovereign_dir = self.root / ".sovereign"
        db_path = sovereign_dir / "sovereign.db"
        jsonl_path = sovereign_dir / "trace.jsonl"

        self.firewall = SovereignFirewall()
        self.budget = BudgetGovernor(db_path=db_path)
        self.ledger = TraceLedger(jsonl_path=jsonl_path, db_path=db_path)
        self.memory = BiTemporalMemory(db_path=db_path)
        self.drift = DriftLock()

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "sovereign_firewall_scan":
            if args.get("tool_name"):
                verdict = self.firewall.scan_tool_call(
                    str(args["tool_name"]),
                    args.get("tool_input") or {},
                    workspace_root=str(self.root),
                )
            else:
                verdict = self.firewall.scan_text(str(args.get("text") or ""), context="mcp")
            return verdict.to_dict()

        if name == "sovereign_budget_status":
            return self.budget.check_budget(str(args.get("session_id") or "default")).to_dict()

        if name == "sovereign_trace_inspect":
            ok, total, msg = self.ledger.verify_chain()
            events = self.ledger.list_events(
                session_id=args.get("session_id"),
                limit=int(args.get("limit", 50)),
            )
            return {
                "chain_valid": ok,
                "total_chain_events": total,
                "chain_status": msg,
                "events": [e.to_dict() for e in events],
            }

        if name == "sovereign_trace_replay":
            return self.ledger.replay(
                session_id=str(args.get("session_id") or "default"),
                from_seq=int(args.get("from_seq", 0)),
                counterfactual_cap_usd=args.get("counterfactual_cap_usd"),
            )

        if name == "sovereign_memory_query":
            facts = self.memory.query(
                subject=args.get("subject"),
                predicate=args.get("predicate"),
                as_of_valid=args.get("as_of_valid"),
                limit=int(args.get("limit", 20)),
            )
            return {"count": len(facts), "facts": [f.to_dict() for f in facts]}

        if name == "sovereign_memory_record":
            fact = self.memory.record(
                subject=str(args["subject"]),
                predicate=str(args["predicate"]),
                obj=str(args["object"]),
                session_id=str(args.get("session_id") or "global"),
                tags=args.get("tags"),
            )
            return fact.to_dict()

        if name == "sovereign_sdd_verify":
            res = self.drift.verify_spec_contract(
                self.root,
                modified_files=args.get("modified_files"),
            )
            return res.to_dict()

        raise ValueError(f"Unknown tool: {name}")

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "claude-code-sovereign", "version": "0.1.0"},
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": MCP_TOOLS},
            }

        if method == "tools/call":
            tool_name = str(params.get("name") or "")
            tool_args = params.get("arguments") or {}
            try:
                result = self.call_tool(tool_name, tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                        "isError": False,
                    },
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def serve_stdio(self) -> int:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                continue
            resp = self.handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        return 0
