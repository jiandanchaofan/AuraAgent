"""Lightweight static pattern scanner for LLM-generated skill code shown
during human review (propose_new_skill's HITL confirmation) — NOT a
sandbox, NOT a security boundary, purely a "look here first" aid that
surfaces common risky patterns (spawning processes, dynamic code eval,
raw sockets, unbounded network calls, filesystem writes/deletes) so a
human reviewer's attention goes to the right lines instead of having to
re-derive this from scratch by reading every line equally carefully.
"""
from __future__ import annotations

import re

_RISKY_PATTERNS: list[tuple[str, str]] = [
    (r"\bimport\s+subprocess\b", "spawns subprocesses (subprocess module)"),
    (r"\bos\.system\s*\(", "runs a shell command (os.system)"),
    (r"\bos\.popen\s*\(", "runs a shell command (os.popen)"),
    (r"\beval\s*\(", "evaluates arbitrary code (eval)"),
    (r"\bexec\s*\(", "executes arbitrary code (exec)"),
    (r"\bimport\s+socket\b", "opens raw network sockets (socket module)"),
    (r"\bimport\s+(requests|urllib|httpx)\b", "makes network requests"),
    (r"""\bopen\s*\([^)]*[\"'][wa][b\"']""", "writes to the filesystem (open(..., 'w'/'a'))"),
    (r"\bshutil\.(rmtree|move)\s*\(", "deletes or moves files/directories (shutil)"),
    (r"\bos\.remove\s*\(", "deletes a file (os.remove)"),
    (r"\b__import__\s*\(", "dynamically imports a module (__import__)"),
]


def scan_for_risky_patterns(code: str) -> list[str]:
    warnings: list[str] = []
    for line_no, line in enumerate(code.splitlines(), start=1):
        for pattern, description in _RISKY_PATTERNS:
            if re.search(pattern, line):
                warnings.append(f"Line {line_no}: {description} -- {line.strip()}")
    return warnings
