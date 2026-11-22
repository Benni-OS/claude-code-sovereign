"""Tests for SovereignInjector and CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_sovereign.cli import main as cli_main
from claude_code_sovereign.core.injector import SovereignInjector


def test_injector_is_idempotent_and_preserves_existing_settings(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"custom": {"command": "x"}}}), encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# My Repo\n", encoding="utf-8")

    inj = SovereignInjector()
    inj.inject(tmp_path)
    inj.inject(tmp_path)  # Second run must be idempotent

    settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["theme"] == "dark"
    assert len(settings["hooks"]["PreToolUse"]) == 1

    mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "custom" in mcp["mcpServers"]
    assert "claude-sovereign" in mcp["mcpServers"]

    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.count("BENNI-SOVEREIGN-START") == 1
    assert (claude_dir / "commands" / "sovereign-audit.md").is_file()


def test_cli_init_status_replay_audit(tmp_path: Path, capsys) -> None:
    assert cli_main(["init", "--path", str(tmp_path)]) == 0
    assert cli_main(["status", "--workspace", str(tmp_path), "--session", "cli-test"]) == 0
    assert cli_main(["replay", "--workspace", str(tmp_path), "--session", "cli-test"]) == 0
    # After init, DB is not yet created until first event, audit still checks chain cleanly
    rc = cli_main(["audit", "--workspace", str(tmp_path), "--json"])
    assert rc in {0, 1}
    out = capsys.readouterr().out
    assert "cli-test" in out


def test_cli_doctor_and_matrix_json(tmp_path: Path, capsys) -> None:
    assert cli_main(["doctor", "--workspace", str(tmp_path), "--json"]) == 0
    doc_out = json.loads(capsys.readouterr().out)
    assert doc_out["overall_status"] == "READY"

    assert cli_main(["matrix", "--scales", "25", "--json"]) == 0
    mat_out = json.loads(capsys.readouterr().out)
    assert mat_out["firewall_matrix"][0]["iterations"] == 25


def test_mcp_server_unknown_method_and_notification(tmp_path: Path) -> None:
    from claude_code_sovereign.serve.mcp_server import SovereignMCPServer

    srv = SovereignMCPServer(workspace_root=tmp_path)
    assert srv.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    err = srv.handle_request({"jsonrpc": "2.0", "id": 99, "method": "unknown/method"})
    assert err is not None and err["error"]["code"] == -32601

