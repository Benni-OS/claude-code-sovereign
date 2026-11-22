"""Tests for TraceLedger SHA-256 chain, offline replay, DriftLock, and BiTemporalMemory."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from claude_code_sovereign.core.bitemporal_memory import BiTemporalMemory
from claude_code_sovereign.core.drift_lock import DriftLock
from claude_code_sovereign.core.trace_ledger import GENESIS_HASH, TraceLedger


def test_trace_ledger_sha256_chain_and_tamper_detection(tmp_path: Path) -> None:
    db = tmp_path / "sov.db"
    jsonl = tmp_path / "trace.jsonl"
    ledger = TraceLedger(jsonl_path=jsonl, db_path=db)

    e1 = ledger.append("s1", "PreToolUse", {"command": "ls"}, tool_name="Bash", decision="allow", cost_usd=0.10)
    e2 = ledger.append("s1", "PreToolUse", {"command": "pytest"}, tool_name="Bash", decision="allow", cost_usd=0.20)
    assert e1.prev_hash == GENESIS_HASH
    assert e2.prev_hash == e1.event_hash

    ok, count, _ = ledger.verify_chain()
    assert ok and count == 2
    assert jsonl.is_file()

    # Tamper with SQLite row directly and verify detection
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE trace_events SET decision = 'deny' WHERE seq = 1")
    ok_after, _, msg = ledger.verify_chain()
    assert not ok_after
    assert "tamper" in msg.lower()


def test_trace_ledger_counterfactual_replay(tmp_path: Path) -> None:
    ledger = TraceLedger(jsonl_path=tmp_path / "t.jsonl", db_path=tmp_path / "s.db")
    ledger.append("s-rep", "PreToolUse", {"command": "echo 1"}, tool_name="Bash", decision="allow", cost_usd=0.30)
    ledger.append("s-rep", "PreToolUse", {"command": "echo 2"}, tool_name="Bash", decision="allow", cost_usd=0.40)

    rep = ledger.replay("s-rep", counterfactual_cap_usd=0.50)
    assert rep["replay_api_cost_usd"] == 0.0
    assert rep["replayed_events"] == 2
    assert rep["divergence_count"] == 1
    assert rep["divergences"][0]["seq"] == 2


def test_bitemporal_memory_record_query_and_invalidate(tmp_path: Path) -> None:
    mem = BiTemporalMemory(db_path=tmp_path / "m.db")
    mem.record("db_engine", "is", "SQLite3 WAL", session_id="s1")
    mem.record("auth", "uses", "Ed25519", session_id="s1")

    active = mem.query(subject="db_engine")
    assert len(active) == 1
    assert active[0].obj == "SQLite3 WAL"

    closed = mem.invalidate("db_engine", predicate="is")
    assert closed == 1
    assert mem.query(subject="db_engine") == []

    compact_txt = mem.export_compact_context()
    assert "Ed25519" in compact_txt
    assert "SQLite3 WAL" not in compact_txt


def test_drift_lock_python_js_and_spec(tmp_path: Path) -> None:
    good_py = tmp_path / "ok.py"
    good_py.write_text("x = 1 + 2\n", encoding="utf-8")
    bad_js = tmp_path / "bad.js"
    bad_js.write_text("function f() { return (1 + 2; }\n", encoding="utf-8")

    dl = DriftLock()
    res_ok = dl.verify_spec_contract(tmp_path, modified_files=["ok.py"])
    assert res_ok.passed

    res_bad = dl.verify_spec_contract(tmp_path, modified_files=["bad.js"])
    assert not res_bad.passed
    assert len(res_bad.syntax_errors) == 1


def test_drift_lock_spec_json_required_and_forbidden(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "spec.json").write_text(
        '{"required_files": ["README.md"], "forbidden_patterns": ["TODO_SECURITY_BYPASS"]}',
        encoding="utf-8",
    )
    py_file = tmp_path / "app.py"
    py_file.write_text("# TODO_SECURITY_BYPASS\nx = 1\n", encoding="utf-8")

    res = DriftLock().verify_spec_contract(tmp_path, modified_files=["app.py"])
    assert not res.passed
    assert any("README.md" in v for v in res.spec_violations)
    assert any("TODO_SECURITY_BYPASS" in v for v in res.spec_violations)


def test_bitemporal_memory_as_of_valid_time(tmp_path: Path) -> None:
    mem = BiTemporalMemory(db_path=tmp_path / "bt.db")
    mem.record(
        "runtime",
        "version",
        "v1",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_until="2026-06-01T00:00:00+00:00",
    )
    mem.record(
        "runtime",
        "version",
        "v2",
        valid_from="2026-06-01T00:00:00+00:00",
        valid_until=None,
    )
    q_jan = mem.query(subject="runtime", as_of_valid="2026-03-01T00:00:00+00:00")
    assert len(q_jan) == 1 and q_jan[0].obj == "v1"

    q_now = mem.query(subject="runtime")
    assert len(q_now) == 1 and q_now[0].obj == "v2"

