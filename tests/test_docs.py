"""
Documentation integrity — the docs are part of the test suite.

Every stale number found on 2026-11-18 (README 71 routes, docs/04 71, docs/09 69 routes and 28 tests,
docs/03 21 collections, PLANS.md 15 documents) was true when written. That is the failure mode: prose
numbers rot silently, and the docs are what a new engineer, a customer's CTO or an AI assistant reads
first. So the counts are generated (`scripts/doc_facts.py`) and a test fails if any doc drifts again.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.doc_facts as doc_facts  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/doc_facts.py", *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_generated_facts_block_matches_the_codebase():
    result = run("--check", "--offline")
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_document_quotes_a_stale_count():
    result = run("--check", "--offline")
    stale = [line for line in (result.stdout + result.stderr).splitlines() if line.strip().startswith("✗")]
    assert not stale, "stale claims:\n" + "\n".join(stale)


def test_the_measured_numbers_are_the_ones_the_code_reports():
    facts = doc_facts.facts()
    assert facts["api_routes"] == doc_facts.count_routes() > 100
    # a floor, not an equality: a hand-typed exact count inside the test that exists
    # to stop hand-typed counts is exactly how this suite went red on v1.12.0
    assert facts["collections"] == doc_facts.count_collections() >= 26
    # a test file that is not counted would silently shrink the safety net
    assert facts["test_files"] >= 13
    assert facts["docs"] >= 22


def test_every_doc_cited_in_the_readme_exists():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    cited = set(re.findall(r"docs/([0-9]{2}-[A-Z0-9\-]+\.md)", readme))
    assert cited, "README cites no documents"
    for name in cited:
        assert os.path.exists(os.path.join(ROOT, "docs", name)), f"README cites missing docs/{name}"
    # and nothing in docs/ is orphaned from the chain
    chain = {f for f in os.listdir(os.path.join(ROOT, "docs")) if f.endswith(".md")}
    assert chain - cited == set(), f"documents not in the README chain: {sorted(chain - cited)}"


def test_the_gap_register_names_its_own_closures():
    """A gap without a 'proof of closure' line is a wish, not a gap."""
    plans = open(os.path.join(ROOT, "PLANS.md"), encoding="utf-8").read()
    assert "## 9. Gap register" in plans
    rows = [line for line in plans.splitlines() if re.match(r"\| \*\*G\d+\*\*", line)]
    assert len(rows) >= 6, "gap register is shorter than the last audit"
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) >= 4 and cells[-1], f"gap row has no proof-of-closure: {row[:80]}"
