#!/usr/bin/env python3
"""
Simple helper to run py_compile over the hawk package and surface syntax errors
in a human-friendly way. Useful to catch truncated/partial commits that cause
"Agent Exception Detected" due to SyntaxError/IndentationError at import time.
"""
from __future__ import annotations

import os
import sys
import py_compile
import traceback
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
HAWK_DIR = ROOT / "hawk"


def _find_py_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*.py"):
        files.append(p)
    return files


def main() -> int:
    py_files = _find_py_files(HAWK_DIR)
    if not py_files:
        print("No Python files found under", HAWK_DIR)
        return 0

    failures = 0
    for p in sorted(py_files):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            failures += 1
            print(f"\n[SYNTAX ERROR] {p}")
            # PyCompileError .msg usually contains the traceback-like info
            print(e.msg)
        except Exception:
            failures += 1
            print(f"\n[ERROR] Unexpected error compiling {p}")
            traceback.print_exc()

    if failures:
        print(f"\npy_compile found {failures} problem(s) in hawk package.")
        return 2
    print("py_compile: hawk package OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())