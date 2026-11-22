"""CLI entrypoint (`claude-sovereign`) for Claude Code Sovereign."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.budget_governor import BudgetGovernor
from .core.hook_engine import HookEngine
from .core.injector import SovereignInjector
from .core.trace_ledger import TraceLedger
from .serve.health_monitor import HealthMonitor
from .serve.mcp_server import SovereignMCPServer
from .serve.telemetry import SovereignTelemetry
from .tools.bench_matrix import run_matrix
from .tools.doctor import run_doctor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-sovereign",
        description="Zero-Dependency Runtime Governance Kernel for Claude Code (Benni OS)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Inject .claude/ hooks, .mcp.json, CLAUDE.md, and slash commands")
    p_init.add_argument("--path", default=".", help="Target repository root")
    p_init.add_argument("--json", action="store_true", help="Output JSON")

    p_hook = sub.add_parser("hook", help="Dispatch an Anthropic Claude Code lifecycle hook via stdin/stdout")
    p_hook.add_argument("event", choices=["PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"])
    p_hook.add_argument("--workspace", default=".", help="Workspace root")
    p_hook.add_argument("--cap-usd", type=float, default=5.0, help="Session USD budget cap")

    p_mcp = sub.add_parser("mcp", help="Start zero-dependency JSON-RPC 2.0 stdio MCP server")
    p_mcp.add_argument("--workspace", default=".", help="Workspace root")

    p_doc = sub.add_parser("doctor", help="Run environment & hook pipeline diagnostics")
    p_doc.add_argument("--workspace", default=".", help="Workspace root")
    p_doc.add_argument("--json", action="store_true", help="Output JSON")

    p_mat = sub.add_parser("matrix", help="Run multi-scale firewall & hook latency benchmark matrix")
    p_mat.add_argument("--scales", nargs="+", type=int, default=[100, 500, 1000], help="Iteration scales")
    p_mat.add_argument("--json", action="store_true", help="Output JSON")

    p_stat = sub.add_parser("status", help="Show budget utilization and telemetry")
    p_stat.add_argument("--workspace", default=".", help="Workspace root")
    p_stat.add_argument("--session", default="default", help="Session ID")
    p_stat.add_argument("--json", action="store_true", help="Output JSON")

    p_rep = sub.add_parser("replay", help="Run $0.00 deterministic offline counterfactual trace replay")
    p_rep.add_argument("--workspace", default=".", help="Workspace root")
    p_rep.add_argument("--session", default="default", help="Session ID")
    p_rep.add_argument("--from-seq", type=int, default=0, help="Start sequence number")
    p_rep.add_argument("--cap-usd", type=float, default=None, help="Counterfactual USD cap")

    p_aud = sub.add_parser("audit", help="Verify SHA-256 chain & .claude/ workspace integrity")
    p_aud.add_argument("--workspace", default=".", help="Workspace root")
    p_aud.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        res = SovereignInjector().inject(args.path)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[Sovereign] Initialized workspace: {res['workspace']}")
            print(f"[Sovereign] Registered hooks: {', '.join(res['hooks_registered'])}")
            print(f"[Sovereign] Files updated: {len(res['injected_files'])}")
        return 0

    if args.command == "hook":
        return HookEngine(workspace_root=args.workspace, budget_cap_usd=args.cap_usd).dispatch_stdin(args.event)

    if args.command == "mcp":
        return SovereignMCPServer(workspace_root=args.workspace).serve_stdio()

    if args.command == "doctor":
        rep = run_doctor(workspace_root=args.workspace, as_json=args.json)
        return 0 if rep["overall_status"] == "READY" else 1

    if args.command == "matrix":
        run_matrix(scales=args.scales, as_json=args.json)
        return 0

    if args.command == "status":
        root = Path(args.workspace).resolve()
        db_path = root / ".sovereign" / "sovereign.db"
        jsonl_path = root / ".sovereign" / "trace.jsonl"
        budget = BudgetGovernor(db_path=db_path).check_budget(args.session).to_dict()
        telem = SovereignTelemetry(TraceLedger(jsonl_path=jsonl_path, db_path=db_path)).snapshot(args.session)
        out = {"budget": budget, "telemetry": telem}
        print(json.dumps(out, indent=2))
        return 0

    if args.command == "replay":
        root = Path(args.workspace).resolve()
        db_path = root / ".sovereign" / "sovereign.db"
        jsonl_path = root / ".sovereign" / "trace.jsonl"
        res = TraceLedger(jsonl_path=jsonl_path, db_path=db_path).replay(
            session_id=args.session,
            from_seq=args.from_seq,
            counterfactual_cap_usd=args.cap_usd,
        )
        print(json.dumps(res, indent=2))
        return 0

    if args.command == "audit":
        res = HealthMonitor(args.workspace).check_all()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[Sovereign Audit] Workspace={res['workspace']} Healthy={res['healthy']}")
            for k, v in res["checks"].items():
                print(f"  - {k}: ok={v['ok']}")
        return 0 if res["healthy"] else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
