#!/usr/bin/env python3
"""Compile all hawk/*.py files to catch SyntaxError/IndentationError before runtime.

Usage:
    python scripts/py_compile_all.py

Exits with non-zero status if any file fails to compile.
"""
from __future__ import annotations

import logging
import os
import pathlib
import py_compile
import sys
from typing import List

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("py_compile_all")


def compile_files(paths: List[pathlib.Path]) -> int:
    """Compile all provided Python files. Returns number of failures."""
    failures = 0
    for p in paths:
        try:
            log.info("Compiling %s", p)
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            failures += 1
            log.error("Failed: %s -> %s", p, e)
    return failures


def find_hawk_files(base_dir: pathlib.Path) -> List[pathlib.Path]:
    """Return list of .py files under hawk/ (non-recursive submodules included)."""
    hawk_dir = base_dir / "hawk"
    if not hawk_dir.exists():
        log.error("hawk/ directory not found at %s", hawk_dir)
        return []
    files: List[pathlib.Path] = []
    for p in hawk_dir.rglob("*.py"):
        # skip tests or migrations if you want; include everything for strict checking
        files.append(p)
    return sorted(files)


def main() -> None:
    repo_root = pathlib.Path(__file__).parent.parent.resolve()
    files = find_hawk_files(repo_root)
    if not files:
        log.error("No hawk Python files found to compile.")
        sys.exit(2)
    failures = compile_files(files)
    if failures:
        log.error("Compilation failed for %d file(s).", failures)
        sys.exit(1)
    log.info("All %d hawk Python files compiled successfully.", len(files))
    sys.exit(0)


if __name__ == "__main__":
    main()