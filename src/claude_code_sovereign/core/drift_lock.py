"""AST syntax verifier and Spec-Driven Reality Lock gate for Stop/SubagentStop."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})\b", re.MULTILINE)


@dataclass(frozen=True)
class DriftCheckResult:
    """Result of AST syntax and Spec-Driven Reality Lock verification."""

    passed: bool
    checked_files: list[str] = field(default_factory=list)
    syntax_errors: list[dict[str, Any]] = field(default_factory=list)
    spec_violations: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checked_files": self.checked_files,
            "syntax_errors": self.syntax_errors,
            "spec_violations": self.spec_violations,
            "summary": self.summary,
        }


class DriftLock:
    """Verifies AST syntax and spec invariants before allowing Claude Code to stop."""

    @staticmethod
    def verify_python_ast(file_path: Path) -> tuple[bool, str | None]:
        try:
            source = file_path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(file_path))
            return True, None
        except SyntaxError as exc:
            return False, f"Line {exc.lineno}: {exc.msg}"
        except OSError as exc:
            return False, f"IO error: {exc}"

    @staticmethod
    def verify_js_ts_syntax(file_path: Path) -> tuple[bool, str | None]:
        """Lightweight zero-dependency JS/TS delimiter & conflict-marker check."""
        try:
            source = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"IO error: {exc}"

        if _CONFLICT_MARKER_RE.search(source):
            return False, "Unresolved Git merge conflict marker detected"

        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = {")", "]", "}"}
        stack: list[str] = []
        in_string: str | None = None
        escaped = False

        for ch in source:
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string is not None:
                escaped = True
                continue
            if in_string is not None:
                if ch == in_string:
                    in_string = None
                continue
            if ch in {"'", '"', "`"}:
                in_string = ch
                continue
            if ch in pairs:
                stack.append(pairs[ch])
            elif ch in closing:
                if not stack or stack[-1] != ch:
                    return False, f"Unbalanced delimiter '{ch}'"
                stack.pop()

        if stack:
            return False, f"Unclosed delimiter(s): expected {''.join(reversed(stack))}"
        return True, None

    def verify_spec_contract(
        self,
        workspace: str | Path,
        modified_files: list[str] | None = None,
    ) -> DriftCheckResult:
        root = Path(workspace)
        targets: list[Path] = []
        if modified_files:
            for rel in modified_files:
                p = Path(rel)
                full = p if p.is_absolute() else (root / p)
                if full.is_file():
                    targets.append(full)
        else:
            for ext in ("*.py", "*.js", "*.ts", "*.tsx", "*.jsx"):
                for p in root.rglob(ext):
                    if any(part in {".git", "node_modules", ".venv", "__pycache__", ".sovereign"} for part in p.parts):
                        continue
                    targets.append(p)
                    if len(targets) >= 200:
                        break

        checked: list[str] = []
        syntax_errors: list[dict[str, Any]] = []
        spec_violations: list[str] = []

        for path in targets:
            rel_str = str(path.relative_to(root)) if root in path.parents else str(path)
            checked.append(rel_str)
            suffix = path.suffix.lower()
            if suffix == ".py":
                ok, err = self.verify_python_ast(path)
            elif suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
                ok, err = self.verify_js_ts_syntax(path)
            else:
                continue

            if not ok and err:
                syntax_errors.append({"file": rel_str, "error": err})

        # Check optional .claude/spec.json contract
        spec_file = root / ".claude" / "spec.json"
        if spec_file.is_file():
            try:
                spec_data = json.loads(spec_file.read_text(encoding="utf-8"))
                for req_file in spec_data.get("required_files", []):
                    if not (root / req_file).exists():
                        spec_violations.append(f"Missing required spec file: {req_file}")
                for forbidden in spec_data.get("forbidden_patterns", []):
                    pat = re.compile(forbidden)
                    for path in targets:
                        content = path.read_text(encoding="utf-8", errors="ignore")
                        if pat.search(content):
                            spec_violations.append(f"Forbidden pattern '{forbidden}' found in {path.name}")
            except (OSError, json.JSONDecodeError) as exc:
                spec_violations.append(f"Invalid .claude/spec.json: {exc}")

        passed = not syntax_errors and not spec_violations
        summary = (
            f"Reality Lock PASSED ({len(checked)} files verified)"
            if passed
            else f"Reality Lock BLOCKED ({len(syntax_errors)} syntax errors, {len(spec_violations)} spec violations)"
        )
        return DriftCheckResult(
            passed=passed,
            checked_files=checked,
            syntax_errors=syntax_errors,
            spec_violations=spec_violations,
            summary=summary,
        )
