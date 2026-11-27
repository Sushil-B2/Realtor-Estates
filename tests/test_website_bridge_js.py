"""Exercise website bridge failure and success mapping with isolated mocks."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_website_bridge_scenarios():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the website bridge regression')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/js/test-website-bridge.cjs')],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout
