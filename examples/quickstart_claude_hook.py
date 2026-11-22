"""Quickstart: Simulate Anthropic Claude Code PreToolUse & Stop hooks with HookEngine."""

from __future__ import annotations

import json
import tempfile

from claude_code_sovereign.core.hook_engine import HookEngine


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = HookEngine(workspace_root=tmp, budget_cap_usd=2.00)

        # 1. Safe Bash tool call
        safe_resp = engine.handle_event(
            "PreToolUse",
            {"session_id": "demo-01", "tool_name": "Bash", "tool_input": {"command": "pytest -v"}},
        )
        print("Safe PreToolUse:", json.dumps(safe_resp, indent=2))

        # 2. Malicious prompt injection / destructive command
        blocked_resp = engine.handle_event(
            "PreToolUse",
            {"session_id": "demo-01", "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
        )
        print("Blocked PreToolUse:", json.dumps(blocked_resp, indent=2))

        # 3. Verify SHA-256 chain
        ok, count, msg = engine.ledger.verify_chain()
        print(f"SHA-256 Chain: valid={ok}, events={count}, detail={msg}")


if __name__ == "__main__":
    main()
