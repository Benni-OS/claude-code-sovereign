"""Tests for MCP server, Telemetry, HealthMonitor, Doctor, and BenchMatrix."""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_sovereign.core.hook_engine import HookEngine
from claude_code_sovereign.core.injector import SovereignInjector
from claude_code_sovereign.serve.health_monitor import HealthMonitor
from claude_code_sovereign.serve.mcp_server import MCP_TOOLS, SovereignMCPServer
from claude_code_sovereign.serve.telemetry import SovereignTelemetry
from claude_code_sovereign.tools.bench_matrix import run_matrix
from claude_code_sovereign.tools.doctor import run_doctor


def test_mcp_server_protocol_and_all_7_tools(tmp_path: Path) -> None:
    srv = SovereignMCPServer(workspace_root=tmp_path)

    init_resp = srv.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "claude-code-sovereign"

    list_resp = srv.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert list_resp is not None
    assert len(list_resp["result"]["tools"]) == len(MCP_TOOLS) == 7

    scan = srv.call_tool("sovereign_firewall_scan", {"text": "sk-ant-api03-1234567890abcdefghij"})
    assert not scan["allowed"]

    rec = srv.call_tool("sovereign_memory_record", {"subject": "cache", "predicate": "uses", "object": "WAL"})
    assert rec["subject"] == "cache"

    qry = srv.call_tool("sovereign_memory_query", {"subject": "cache"})
    assert qry["count"] == 1

    bud = srv.call_tool("sovereign_budget_status", {"session_id": "default"})
    assert bud["state"] == "OK"

    trc = srv.call_tool("sovereign_trace_inspect", {})
    assert trc["chain_valid"]

    rep = srv.call_tool("sovereign_trace_replay", {"session_id": "default"})
    assert rep["replay_api_cost_usd"] == 0.0

    sdd = srv.call_tool("sovereign_sdd_verify", {})
    assert sdd["passed"]


def test_telemetry_and_health_monitor(tmp_path: Path) -> None:
    SovereignInjector().inject(tmp_path)
    engine = HookEngine(workspace_root=tmp_path)
    engine.handle_event("PreToolUse", {"session_id": "t1", "tool_name": "Bash", "tool_input": {"command": "ls"}})
    engine.handle_event("PreToolUse", {"session_id": "t1", "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})

    telem = SovereignTelemetry(engine.ledger).snapshot("t1")
    assert telem["total_events"] == 2
    assert telem["blocked_events"] == 1
    assert telem["estimated_usd_saved"] > 0
    assert telem["latency_ms"]["p50"] >= 0.0

    health = HealthMonitor(tmp_path).check_all()
    assert health["healthy"]


def test_doctor_and_matrix(tmp_path: Path) -> None:
    doc = run_doctor(workspace_root=tmp_path, as_json=False)
    assert doc["overall_status"] == "READY"
    assert doc["hook_pipeline_probe"]["passed"]

    mat = run_matrix(scales=[50, 100], as_json=False)
    assert len(mat["firewall_matrix"]) == 2
    assert mat["e2e_hook_ledger"]["sha256_verified"]
