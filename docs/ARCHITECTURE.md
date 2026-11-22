# Architecture — `claude-code-sovereign`

> **Benni Master OS v9.4 Tier-0 Runtime Governance Kernel for Claude Code**

`claude-code-sovereign` is a **zero-dependency** Python 3.10+ runtime governance kernel that wraps Anthropic's Claude Code CLI via official Lifecycle Hooks (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStop`) and a native JSON-RPC 2.0 stdio MCP server.

---

## 1. Execution Pipeline

```text
  Claude Code CLI / Subagents
         │
         ├──► UserPromptSubmit Hook
         │       ├── SovereignFirewall.scan_text() ──► BLOCK if injection/secret
         │       └── BiTemporalMemory.export_compact_context() ──► Inject invariants
         │
         ├──► PreToolUse Hook (Bash / Read / Write / Edit / WebFetch)
         │       ├── BudgetGovernor.check_budget() ──► DENY if >= 100% cap (WARN >= 80%)
         │       ├── SovereignFirewall.scan_tool_call() ──► DENY if SHL/SEC/PTH/INJ
         │       └── TraceLedger.append() ──► Seal SHA-256 event to JSONL + SQLite3
         │
         ├──► PostToolUse Hook
         │       ├── BudgetGovernor.record_usage() ──► Track per-subagent USD & tokens
         │       └── TraceLedger.append() ──► Record latency_ms & cost_usd
         │
         └──► Stop / SubagentStop Hook
                 └── DriftLock.verify_spec_contract() ──► BLOCK if AST syntax or spec fails
```

---

## 2. Core Subsystems

| Module | Responsibility | Storage / Protocol |
|---|---|---|
| `core/hook_engine.py` | Official Claude Code JSON stdin/stdout dispatcher | Stdin/Stdout JSON |
| `core/firewall.py` | Sub-ms prompt injection, secret exfiltration, shell & path traversal shield | Compiled Regex + Path Resolution |
| `core/budget_governor.py` | Per-session & per-subagent USD/token circuit breaker (80% warn, 100% block) | SQLite3 WAL (`budget_ledger`) |
| `core/trace_ledger.py` | Immutable SHA-256 hash-chained trajectory + `$0.00` counterfactual replay | `.sovereign/trace.jsonl` + SQLite3 |
| `core/drift_lock.py` | Python `ast.parse()` + JS/TS delimiter & `.claude/spec.json` Reality Lock | Local Filesystem AST |
| `core/bitemporal_memory.py` | Valid-time & transaction-time knowledge graph surviving `/compact` & `/clear` | SQLite3 WAL (`bitemporal_facts`) |
| `core/injector.py` | Idempotent `.claude/settings.json`, `.mcp.json`, `CLAUDE.md` & slash commands | JSON + Markdown |
| `serve/mcp_server.py` | Zero-dependency JSON-RPC 2.0 stdio MCP server (7 Sovereign tools) | Stdio JSON-RPC 2.0 |
| `serve/telemetry.py` | Rolling P50/P90/P99 hook latency, blocked attack count & USD saved | In-Memory Aggregation |
| `serve/health_monitor.py` | `.claude/` config, SQLite3 `PRAGMA integrity_check` & SHA-256 chain check | Diagnostic Probe |

---

## 3. Cryptographic Trajectory Integrity

Every hook invocation produces a `TraceEvent` linked to the previous event via `prev_hash`:

$$\text{event\_hash}_n = \text{SHA256}(\text{canonical\_json}(\text{seq}_n, \text{timestamp}_n, \text{session\_id}_n, \text{event\_type}_n, \text{tool\_name}_n, \text{payload}_n, \text{decision}_n, \text{prev\_hash}_{n-1}))$$

Starting from `GENESIS_HASH = "0" * 64`, any post-hoc modification of a SQLite3 row or `.jsonl` entry invalidates `verify_chain()` immediately.

---

## 4. $0.00 Deterministic Counterfactual Replay

`TraceLedger.replay()` re-evaluates recorded tool calls offline against:
1. A tighter `counterfactual_cap_usd` budget cap, or
2. A stricter `SovereignFirewall` policy.

Because all tool inputs and costs are already sealed in the ledger, counterfactual diffing costs **$0.00** in LLM API calls.
