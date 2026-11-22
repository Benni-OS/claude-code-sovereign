"""Idempotent .claude/ ecosystem injector for Claude Code Hooks, MCP, CLAUDE.md, and Slash Commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SOVEREIGN_START = "<!-- BENNI-SOVEREIGN-START -->"
SOVEREIGN_END = "<!-- BENNI-SOVEREIGN-END -->"

CLAUDE_MD_BLOCK = f"""{SOVEREIGN_START}
## 🛡️ Sovereign Governance Kernel (`claude-code-sovereign` — Benni OS)

This repository is protected by **Claude Code Sovereign**, enforcing:
1. **Prompt-Injection & Exfiltration Firewall** (`PreToolUse` / `UserPromptSubmit`)
2. **Economic Circuit Breaker** (80% warning / 100% hard cap via `claude-sovereign status`)
3. **Immutable SHA-256 Trajectory Ledger** (`.sovereign/trace.jsonl` + SQLite3)
4. **Spec-Driven Reality Lock** (AST syntax & spec verification on `Stop` / `SubagentStop`)
5. **Bi-Temporal Cross-Session Memory** (persisted across `/compact` and `/clear`)

Use MCP tools `sovereign_memory_record` and `sovereign_memory_query` to persist architectural invariants.
{SOVEREIGN_END}"""

SLASH_COMMANDS: dict[str, str] = {
    "sovereign-audit.md": (
        "---\ndescription: Run Sovereign SHA-256 chain & security integrity audit\n---\n"
        "Run `claude-sovereign audit` via Bash and summarize any integrity findings or blocked events.\n"
    ),
    "sovereign-replay.md": (
        "---\ndescription: Run $0.00 deterministic offline counterfactual trace replay\n---\n"
        "Run `claude-sovereign replay` via Bash or call `sovereign_trace_replay` via MCP to inspect counterfactual divergences.\n"
    ),
    "sovereign-budget.md": (
        "---\ndescription: Check current session USD/token budget utilization\n---\n"
        "Call `sovereign_budget_status` via MCP or run `claude-sovereign status` and report remaining USD budget.\n"
    ),
    "sovereign-spec.md": (
        "---\ndescription: Verify AST syntax and Spec-Driven Reality Lock\n---\n"
        "Call `sovereign_sdd_verify` via MCP to validate all modified Python/JS/TS files and `.claude/spec.json`.\n"
    ),
}


class SovereignInjector:
    """Idempotently configures .claude/settings.json, .mcp.json, CLAUDE.md, and slash commands."""

    def inject(self, target_dir: str | Path) -> dict[str, Any]:
        root = Path(target_dir).resolve()
        claude_dir = root / ".claude"
        commands_dir = claude_dir / "commands"
        sovereign_dir = root / ".sovereign"

        claude_dir.mkdir(parents=True, exist_ok=True)
        commands_dir.mkdir(parents=True, exist_ok=True)
        sovereign_dir.mkdir(parents=True, exist_ok=True)

        updated_files: list[str] = []

        # 1. .claude/settings.json (Official Claude Code Hooks schema)
        settings_path = claude_dir / "settings.json"
        settings: dict[str, Any] = {}
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                settings = {}

        hooks = settings.setdefault("hooks", {})
        hook_events = {
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "claude-sovereign hook PreToolUse"}]}],
            "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "claude-sovereign hook PostToolUse"}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "claude-sovereign hook UserPromptSubmit"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "claude-sovereign hook Stop"}]}],
            "SubagentStop": [{"hooks": [{"type": "command", "command": "claude-sovereign hook SubagentStop"}]}],
        }
        for event_name, entries in hook_events.items():
            existing = hooks.get(event_name, [])
            if not any("claude-sovereign hook" in json.dumps(item) for item in existing):
                existing.extend(entries)
            hooks[event_name] = existing

        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        updated_files.append(str(settings_path))

        # 2. .mcp.json
        mcp_path = root / ".mcp.json"
        mcp_cfg: dict[str, Any] = {}
        if mcp_path.is_file():
            try:
                mcp_cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                mcp_cfg = {}

        servers = mcp_cfg.setdefault("mcpServers", {})
        servers["claude-sovereign"] = {
            "command": "claude-sovereign",
            "args": ["mcp"],
        }
        mcp_path.write_text(json.dumps(mcp_cfg, indent=2) + "\n", encoding="utf-8")
        updated_files.append(str(mcp_path))

        # 3. CLAUDE.md
        claude_md_path = root / "CLAUDE.md"
        if claude_md_path.is_file():
            content = claude_md_path.read_text(encoding="utf-8")
            if SOVEREIGN_START in content and SOVEREIGN_END in content:
                pre = content.split(SOVEREIGN_START, 1)[0]
                post = content.split(SOVEREIGN_END, 1)[1]
                new_content = pre + CLAUDE_MD_BLOCK + post
            else:
                new_content = content.rstrip() + "\n\n" + CLAUDE_MD_BLOCK + "\n"
        else:
            new_content = CLAUDE_MD_BLOCK + "\n"
        claude_md_path.write_text(new_content, encoding="utf-8")
        updated_files.append(str(claude_md_path))

        # 4. .claude/commands/*.md
        for fname, body in SLASH_COMMANDS.items():
            cmd_path = commands_dir / fname
            cmd_path.write_text(body, encoding="utf-8")
            updated_files.append(str(cmd_path))

        return {
            "workspace": str(root),
            "injected_files": updated_files,
            "hooks_registered": list(hook_events.keys()),
            "mcp_server": "claude-sovereign",
        }
