"""Zero-dependency prompt-injection firewall, credential guard, and shell shield."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FirewallVerdict:
    """Result of scanning a prompt, file path, or tool call."""

    allowed: bool
    risk_score: float
    category: str
    reason: str
    matched_rule: str
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk_score": round(self.risk_score, 4),
            "category": self.category,
            "reason": self.reason,
            "matched_rule": self.matched_rule,
            "latency_ms": round(self.latency_ms, 4),
        }


_INJECTION_RULES: list[tuple[str, float, re.Pattern[str], str]] = [
    (
        "INJ-001",
        0.98,
        re.compile(
            r"\b(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above|system)\s+(instructions?|prompts?|rules?|constraints?)\b",
            re.IGNORECASE,
        ),
        "Prompt injection attempt: instruction override detected",
    ),
    (
        "INJ-002",
        0.95,
        re.compile(
            r"\b(you\s+are\s+now\s+DAN|jailbreak\s+mode|developer\s+mode\s+enabled|bypass\s+safety)\b",
            re.IGNORECASE,
        ),
        "Prompt injection attempt: jailbreak persona or safety bypass",
    ),
    (
        "INJ-003",
        0.92,
        re.compile(
            r"(<\s*system\s*>|\[INST\]|\[/INST\]|<\|im_start\|>system)",
            re.IGNORECASE,
        ),
        "Prompt injection attempt: raw control/role delimiter injection",
    ),
    (
        "INJ-004",
        0.90,
        re.compile(
            r"\b(reveal|print|dump|exfiltrate|send)\s+(your\s+)?(system\s+prompt|hidden\s+instructions?|api\s+keys?|secrets?)\b",
            re.IGNORECASE,
        ),
        "Prompt injection attempt: system prompt or secret exfiltration",
    ),
    (
        "INJ-005",
        0.88,
        re.compile(
            r"<!--[^>]*(ignore\s+previous|exfiltrate|curl\s+http|bash\s+-c)[^>]*-->",
            re.IGNORECASE | re.DOTALL,
        ),
        "Indirect prompt injection hidden inside HTML/Markdown comment",
    ),
]

_SECRET_RULES: list[tuple[str, float, re.Pattern[str], str]] = [
    (
        "SEC-001",
        1.00,
        re.compile(r"sk-ant-(?:api\d+-)?[A-Za-z0-9_-]{16,}"),
        "Anthropic API key (sk-ant-*) detected",
    ),
    (
        "SEC-002",
        1.00,
        re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|github_pat_[A-Za-z0-9_]{20,}"),
        "GitHub personal access token detected",
    ),
    (
        "SEC-003",
        0.98,
        re.compile(r"\bAKIA[0-9A-Z]{16}\b|AWS_SECRET_ACCESS_KEY\s*=\s*['\"]?[A-Za-z0-9/+=]{20,}"),
        "AWS access key or secret detected",
    ),
    (
        "SEC-004",
        0.98,
        re.compile(r"-----BEGIN\s+(?:(?:RSA|EC|DSA|OPENSSH|PGP|ENCRYPTED)\s+)?(?:PRIVATE\s+)?KEY-----"),
        "Private cryptographic key material detected",
    ),
    (
        "SEC-005",
        0.95,
        re.compile(r"\b(sk-proj-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b"),
        "Cloud/SaaS API credential (OpenAI/Slack/Google) detected",
    ),
]

_SENSITIVE_FILE_PATTERN = re.compile(
    r"(^|[\\/])(\.env(\.[A-Za-z0-9_-]+)?|id_rsa|id_ed25519|credentials\.json|\.aws[\\/]credentials|\.npmrc|\.pypirc)$",
    re.IGNORECASE,
)

_DESTRUCTIVE_SHELL_RULES: list[tuple[str, float, re.Pattern[str], str]] = [
    (
        "SHL-001",
        1.00,
        re.compile(r"\brm\s+(-[A-Za-z]*r[A-Za-z]*f|-[A-Za-z]*f[A-Za-z]*r|--recursive\s+--force)\s+(/|~|\$HOME|\*)(\s|$)"),
        "Destructive recursive deletion targeting root, home, or wildcard",
    ),
    (
        "SHL-002",
        0.97,
        re.compile(r"\b(curl|wget|fetch)\b[^|;\n]+\|\s*(sudo\s+)?(sh|bash|zsh|pwsh|powershell|python3?)\b", re.IGNORECASE),
        "Remote code execution via piped download-and-execute (curl/wget | sh)",
    ),
    (
        "SHL-003",
        1.00,
        re.compile(r"\b(mkfs(\.[a-z0-9]+)?|dd\s+if=/dev/(zero|urandom)\s+of=/dev/[a-z]+|:\(\)\s*\{\s*:\|:\s*&\s*\}\s*;:)\b"),
        "Disk wipe, raw device overwrite, or fork bomb detected",
    ),
    (
        "SHL-004",
        0.95,
        re.compile(r"\bchmod\s+(-R\s+)?777\s+/(\s|$)|\b(shutdown|reboot|halt|poweroff)\b"),
        "Unsafe root permission change or host power-state command",
    ),
    (
        "SHL-005",
        0.96,
        re.compile(r"\b(cat|head|tail|less|more|cp|scp|curl|nc|tar)\b[^\n]*(\.env\b|id_rsa\b|id_ed25519\b|/etc/shadow\b|\.aws/credentials\b)"),
        "Attempted shell read or exfiltration of sensitive credential file",
    ),
]

_SENSITIVE_SYSTEM_PATHS = (
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
    "/root/.ssh",
    "c:\\windows\\system32",
)


class SovereignFirewall:
    """Sub-millisecond zero-dependency security firewall for Claude Code."""

    def __init__(self, strict_mode: bool = True, extra_blocked_patterns: list[str] | None = None) -> None:
        self.strict_mode = strict_mode
        self.extra_rules: list[tuple[str, float, re.Pattern[str], str]] = []
        for idx, pat in enumerate(extra_blocked_patterns or [], start=1):
            self.extra_rules.append(
                (f"USR-{idx:03d}", 0.90, re.compile(pat, re.IGNORECASE), f"Custom firewall rule matched: {pat}")
            )

    def scan_text(self, text: str, context: str = "prompt") -> FirewallVerdict:
        """Scan arbitrary text (prompt, fetched web content, tool output) for injections and secrets."""
        t0 = time.perf_counter()
        if not text:
            return FirewallVerdict(True, 0.0, "clean", "Empty input", "NONE", (time.perf_counter() - t0) * 1000.0)

        for rule_id, score, pattern, reason in _INJECTION_RULES:
            if pattern.search(text):
                return FirewallVerdict(
                    False,
                    score,
                    "prompt_injection",
                    f"[{context}] {reason}",
                    rule_id,
                    (time.perf_counter() - t0) * 1000.0,
                )

        for rule_id, score, pattern, reason in _SECRET_RULES:
            if pattern.search(text):
                return FirewallVerdict(
                    False,
                    score,
                    "secret_exfiltration",
                    f"[{context}] {reason}",
                    rule_id,
                    (time.perf_counter() - t0) * 1000.0,
                )

        for rule_id, score, pattern, reason in self.extra_rules:
            if pattern.search(text):
                return FirewallVerdict(
                    False,
                    score,
                    "custom_policy",
                    f"[{context}] {reason}",
                    rule_id,
                    (time.perf_counter() - t0) * 1000.0,
                )

        return FirewallVerdict(
            True,
            0.0,
            "clean",
            f"[{context}] No threats detected",
            "NONE",
            (time.perf_counter() - t0) * 1000.0,
        )

    def _check_path_safety(self, candidate: str, workspace_root: str | None) -> FirewallVerdict | None:
        t0 = time.perf_counter()
        normalized = candidate.strip()
        if not normalized:
            return None

        low = normalized.lower()
        for sys_path in _SENSITIVE_SYSTEM_PATHS:
            if sys_path in low:
                return FirewallVerdict(
                    False,
                    0.99,
                    "path_traversal",
                    f"Access to protected system path denied: {candidate}",
                    "PTH-001",
                    (time.perf_counter() - t0) * 1000.0,
                )

        if _SENSITIVE_FILE_PATTERN.search(normalized):
            return FirewallVerdict(
                False,
                0.96,
                "secret_exfiltration",
                f"Access to sensitive credential file blocked: {candidate}",
                "SEC-006",
                (time.perf_counter() - t0) * 1000.0,
            )

        if workspace_root:
            try:
                root = Path(workspace_root).resolve()
                target = Path(normalized)
                if not target.is_absolute():
                    target = (root / target).resolve()
                else:
                    target = target.resolve()
                # Allow temporary directories explicitly if needed? No, strict workspace containment.
                if root != target and root not in target.parents:
                    return FirewallVerdict(
                        False,
                        0.93,
                        "path_traversal",
                        f"Path escapes workspace boundary ({root}): {candidate}",
                        "PTH-002",
                        (time.perf_counter() - t0) * 1000.0,
                    )
            except OSError:
                pass
        elif ".." in normalized.replace("\\", "/").split("/"):
            return FirewallVerdict(
                False,
                0.85,
                "path_traversal",
                f"Relative parent-directory traversal detected: {candidate}",
                "PTH-003",
                (time.perf_counter() - t0) * 1000.0,
            )
        return None

    def scan_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        workspace_root: str | None = None,
    ) -> FirewallVerdict:
        """Scan a Claude Code PreToolUse / PostToolUse payload."""
        t0 = time.perf_counter()
        name_low = (tool_name or "").lower()

        # 1. Shell command inspection (Bash / Terminal / Shell)
        if name_low in {"bash", "shell", "terminal", "execute_command"}:
            cmd = str(tool_input.get("command") or tool_input.get("cmd") or "")
            for rule_id, score, pattern, reason in _DESTRUCTIVE_SHELL_RULES:
                if pattern.search(cmd):
                    return FirewallVerdict(
                        False,
                        score,
                        "destructive_shell",
                        reason,
                        rule_id,
                        (time.perf_counter() - t0) * 1000.0,
                    )
            text_verdict = self.scan_text(cmd, context=f"tool:{tool_name}")
            if not text_verdict.allowed:
                return text_verdict

        # 2. File path inspection (Read, Write, Edit, MultiEdit, Glob, Grep)
        for key in ("file_path", "path", "target_file", "notebook_path"):
            val = tool_input.get(key)
            if isinstance(val, str) and val.strip():
                path_verdict = self._check_path_safety(val, workspace_root)
                if path_verdict is not None:
                    return path_verdict

        # 3. Content & URL inspection across all string fields
        for key, val in tool_input.items():
            if isinstance(val, str) and val.strip():
                if key in {"url", "uri"} and re.match(r"^(file|ftp|gopher)://", val.strip(), re.IGNORECASE):
                    return FirewallVerdict(
                        False,
                        0.91,
                        "ssrf_protocol",
                        f"Disallowed URL scheme in {tool_name}: {val}",
                        "NET-001",
                        (time.perf_counter() - t0) * 1000.0,
                    )
                text_verdict = self.scan_text(val, context=f"tool:{tool_name}.{key}")
                if not text_verdict.allowed:
                    return text_verdict

        return FirewallVerdict(
            True,
            0.0,
            "clean",
            f"Tool call {tool_name} cleared by Sovereign Firewall",
            "NONE",
            (time.perf_counter() - t0) * 1000.0,
        )
