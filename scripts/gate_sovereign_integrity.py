"""Integrity gate script: verifies injector, firewall, SHA-256 ledger, and AST drift lock."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from claude_code_sovereign.core.hook_engine import HookEngine
from claude_code_sovereign.core.injector import SovereignInjector
from claude_code_sovereign.serve.health_monitor import HealthMonitor


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        SovereignInjector().inject(root)
        engine = HookEngine(workspace_root=root)
        engine.handle_event("PreToolUse", {"session_id": "gate", "tool_name": "Read", "tool_input": {"file_path": "app.py"}})
        engine.handle_event("PreToolUse", {"session_id": "gate", "tool_name": "Read", "tool_input": {"file_path": ".env"}})

        health = HealthMonitor(root).check_all()
        if not health["healthy"]:
            print("GATE FAILED:", health)
            return 1
        print("SOVEREIGN INTEGRITY GATE PASSED:", health["checks"]["sha256_chain"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
