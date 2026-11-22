"""Benchmark script for Claude Code Sovereign hook latency and firewall throughput."""

from __future__ import annotations

from claude_code_sovereign.tools.bench_matrix import run_matrix


def main() -> None:
    run_matrix(scales=[200, 1000, 5000], as_json=False)


if __name__ == "__main__":
    main()
