"""Unit tests for the static "look here first" scanner used during
propose_new_skill's human code review. Not a sandbox — just verifying it
reliably flags the documented patterns and doesn't false-positive on
ordinary skill code.
"""
from __future__ import annotations

from tools.self_extend.code_review import scan_for_risky_patterns


def test_clean_code_has_no_warnings():
    code = (
        "import argparse, json\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--args-json', required=True)\n"
        "args = parser.parse_args()\n"
        "payload = json.loads(args.args_json)\n"
        "print(len(payload['text'].split()))\n"
    )
    assert scan_for_risky_patterns(code) == []


def test_flags_subprocess_import():
    warnings = scan_for_risky_patterns("import subprocess\n")
    assert any("subprocess" in w for w in warnings)


def test_flags_os_system():
    warnings = scan_for_risky_patterns("os.system('rm -rf /')\n")
    assert any("os.system" in w for w in warnings)


def test_flags_eval_and_exec():
    warnings = scan_for_risky_patterns("eval('1+1')\nexec('print(1)')\n")
    assert any("eval" in w for w in warnings)
    assert any("exec" in w for w in warnings)


def test_flags_network_imports():
    for module in ("requests", "urllib", "httpx", "socket"):
        warnings = scan_for_risky_patterns(f"import {module}\n")
        assert warnings, f"expected a warning for importing {module}"


def test_flags_file_write():
    warnings = scan_for_risky_patterns("open('out.txt', 'w')\n")
    assert any("filesystem" in w for w in warnings)


def test_flags_shutil_rmtree():
    warnings = scan_for_risky_patterns("shutil.rmtree('/tmp/x')\n")
    assert any("shutil" in w for w in warnings)


def test_warning_includes_line_number():
    code = "x = 1\ny = 2\nimport subprocess\n"
    warnings = scan_for_risky_patterns(code)
    assert any(w.startswith("Line 3:") for w in warnings)
