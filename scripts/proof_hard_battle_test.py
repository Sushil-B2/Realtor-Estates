#!/usr/bin/env python3
"""
Executable 12-Scenario Hard Real-World, Concurrency & Adversarial Battle-Test
Proof (`v1.39.0`). Exercises real human & attacker scenarios end-to-end.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    env = {**os.environ, "RE_DB": "json"}
    print("Running 12-Scenario Real-World, Concurrency & Adversarial Battle-Test Suite...")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_hard_realworld_scenarios.py", "-v"],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        return proc.returncode
    print("ALL REAL-WORLD & ADVERSARIAL BATTLE-TEST SCENARIOS PASSED (0 HTTP 500s, 0 RACE LEAKS)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
