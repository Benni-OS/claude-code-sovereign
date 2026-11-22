"""Quickstart: Full Sovereign session with Bi-Temporal Memory, Budget Governor, and Time-Travel Replay."""

from __future__ import annotations

import json
import tempfile

from claude_code_sovereign.core.hook_engine import HookEngine


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = HookEngine(workspace_root=tmp, budget_cap_usd=1.00)

        # Record persistent architectural invariant
        engine.memory.record(
            subject="auth_service",
            predicate="must_use",
            obj="Ed25519 stateless JWT tokens",
            session_id="demo-02",
            tags=["architecture", "security"],
        )

        # Simulate UserPromptSubmit (injects bi-temporal memory automatically)
        prompt_res = engine.handle_event(
            "UserPromptSubmit",
            {"session_id": "demo-02", "prompt": "Refactor the auth middleware"},
        )
        print("UserPromptSubmit:", json.dumps(prompt_res, indent=2))

        # Simulate PostToolUse token accounting
        engine.handle_event(
            "PostToolUse",
            {
                "session_id": "demo-02",
                "tool_name": "Edit",
                "model": "claude-sonnet-4",
                "usage": {"input_tokens": 120_000, "output_tokens": 20_000},
            },
        )
        print("Budget Status:", json.dumps(engine.budget.check_budget("demo-02").to_dict(), indent=2))

        # $0.00 Offline Counterfactual Replay with tighter $0.25 cap
        replay_res = engine.ledger.replay("demo-02", counterfactual_cap_usd=0.25)
        print("Counterfactual Replay:", json.dumps(replay_res, indent=2))


if __name__ == "__main__":
    main()
