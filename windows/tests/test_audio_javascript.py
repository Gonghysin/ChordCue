"""Run actual shared browser scheduling code with fake AudioContext/clocks."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_browser_scheduler():
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for the shared audio scheduler tests")
    script = Path(__file__).parent / "js" / "metronome.test.cjs"
    result = subprocess.run(
        [node, "--test", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
