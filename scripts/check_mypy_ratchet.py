#!/usr/bin/env python3
"""Fail when mypy errors increase above the checked-in baseline."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASELINE_PATH = Path("mypy_baseline")


def main() -> int:
    baseline = int(BASELINE_PATH.read_text(encoding="utf-8").strip() or "0")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "bot/",
            "core/",
            "main.py",
            "--ignore-missing-imports",
            "--no-error-summary",
        ],
        capture_output=True,
        text=True,
    )
    errors = len(re.findall(r": error:", result.stdout + result.stderr))
    print(f"mypy errors: {errors}; baseline: {baseline}")
    if errors > baseline:
        print(result.stdout)
        print(result.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
