"""Run the actual shared import, notation, playback and device modules in Node."""
from pathlib import Path
import shutil
import subprocess


def test_shared_score_and_device_modules():
    node = shutil.which("node")
    assert node, "Node.js is required for shared score module tests"
    folder = Path(__file__).parent / "js"
    scripts = sorted(str(p) for p in folder.glob("*.test.cjs") if p.name != "metronome.test.cjs")
    result = subprocess.run([node, "--test", "--test-isolation=none", *scripts], capture_output=True,
                            text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
