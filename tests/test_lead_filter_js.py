"""Run the real lead view's filter regression scenarios through Node."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_lead_filter_regression():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the lead filter regression')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/js/test-lead-filter.cjs')],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout
