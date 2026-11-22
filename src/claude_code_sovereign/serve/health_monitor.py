"""Integrity monitor for .claude/ config, SQLite3 WAL database, and SHA-256 chain."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..core.trace_ledger import TraceLedger


class HealthMonitor:
    """Verifies .claude/ hooks, .mcp.json, SQLite3 integrity, and SHA-256 trajectory chain."""

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.root = Path(workspace_root).resolve()
        self.sovereign_dir = self.root / ".sovereign"
        self.db_path = self.sovereign_dir / "sovereign.db"
        self.jsonl_path = self.sovereign_dir / "trace.jsonl"

    def check_all(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}

        # 1. .claude/settings.json
        settings_path = self.root / ".claude" / "settings.json"
        settings_ok = False
        if settings_path.is_file():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
                settings_ok = "hooks" in data and "PreToolUse" in data["hooks"]
            except json.JSONDecodeError:
                settings_ok = False
        checks["claude_settings"] = {"ok": settings_ok, "path": str(settings_path)}

        # 2. .mcp.json
        mcp_path = self.root / ".mcp.json"
        mcp_ok = False
        if mcp_path.is_file():
            try:
                data = json.loads(mcp_path.read_text(encoding="utf-8"))
                mcp_ok = "claude-sovereign" in data.get("mcpServers", {})
            except json.JSONDecodeError:
                mcp_ok = False
        checks["mcp_config"] = {"ok": mcp_ok, "path": str(mcp_path)}

        # 3. CLAUDE.md
        claude_md = self.root / "CLAUDE.md"
        claude_md_ok = claude_md.is_file() and "BENNI-SOVEREIGN-START" in claude_md.read_text(
            encoding="utf-8", errors="ignore"
        )
        checks["claude_md"] = {"ok": claude_md_ok, "path": str(claude_md)}

        # 4. SQLite3 integrity
        sqlite_ok = False
        sqlite_msg = "Database not initialized"
        if self.db_path.is_file():
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=5.0)
                try:
                    res = conn.execute("PRAGMA integrity_check;").fetchone()
                    sqlite_ok = bool(res and res[0] == "ok")
                    sqlite_msg = str(res[0]) if res else "unknown"
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                sqlite_msg = str(exc)
        checks["sqlite_integrity"] = {"ok": sqlite_ok, "detail": sqlite_msg}

        # 5. SHA-256 Chain
        if self.db_path.is_file():
            ledger = TraceLedger(jsonl_path=self.jsonl_path, db_path=self.db_path)
            chain_ok, count, detail = ledger.verify_chain()
        else:
            chain_ok, count, detail = True, 0, "Empty ledger (not yet initialized)"
        checks["sha256_chain"] = {"ok": chain_ok, "events": count, "detail": detail}

        overall = all(bool(v["ok"]) for v in checks.values())
        return {
            "healthy": overall,
            "workspace": str(self.root),
            "checks": checks,
        }
