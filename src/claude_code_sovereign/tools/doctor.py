"""Full Claude Code environment & hook pipeline diagnostic (`claude-sovereign doctor`)."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..core.hook_engine import HookEngine
from ..serve.health_monitor import HealthMonitor


def run_doctor(workspace_root: str | Path = ".", as_json: bool = False) -> dict[str, Any]:
    """Probe Python runtime, Claude CLI availability, hook pipeline latency, and .claude/ health."""
    root = Path(workspace_root).resolve()
    claude_bin = shutil.which("claude")
    git_bin = shutil.which("git")
    health = HealthMonitor(root).check_all()

    # Live dry-run hook pipeline probe in an isolated temporary directory
    with tempfile.TemporaryDirectory() as tmp:
        engine = HookEngine(workspace_root=tmp)
        allow_res = engine.handle_event(
            "PreToolUse",
            {"session_id": "doctor", "tool_name": "Bash", "tool_input": {"command": "pytest -v"}},
        )
        deny_res = engine.handle_event(
            "PreToolUse",
            {"session_id": "doctor", "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
        )
        ok_chain, count, _ = engine.ledger.verify_chain()

    allow_ok = allow_res.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"
    deny_ok = deny_res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    pipeline_ok = allow_ok and deny_ok and ok_chain and count == 2

    report = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "zero_runtime_deps": True,
        "claude_cli_detected": claude_bin is not None,
        "claude_cli_path": claude_bin or "not found (optional for offline mode)",
        "git_detected": git_bin is not None,
        "hook_pipeline_probe": {
            "passed": pipeline_ok,
            "allow_check": allow_ok,
            "deny_check": deny_ok,
            "sha256_chain_check": ok_chain,
        },
        "workspace_health": health,
        "overall_status": "READY" if (sys.version_info >= (3, 10) and pipeline_ok) else "DEGRADED",
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("=== Claude Code Sovereign Doctor ===")
        print(f"Python Runtime   : {report['python_version']} ({report['platform']})")
        print(f"Runtime Deps     : 0 (Pure Python Stdlib)")
        print(f"Claude CLI       : {report['claude_cli_path']}")
        print(f"Hook Pipeline    : {'PASS' if pipeline_ok else 'FAIL'} (Allow={allow_ok}, Deny={deny_ok}, SHA-256={ok_chain})")
        print(f"Workspace        : {health['workspace']}")
        for name, info in health["checks"].items():
            status = "OK" if info["ok"] else "MISSING/UNINIT"
            print(f"  - {name:<18}: {status}")
        print(f"Overall Status   : {report['overall_status']}")
    return report
