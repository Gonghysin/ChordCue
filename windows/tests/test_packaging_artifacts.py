"""Enable final artifact tests with CHORDCUE_MSI / CHORDCUE_FROZEN_DIR."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
from verify_artifacts import read_msi, verify_msi, verify_tree  # noqa: E402


@pytest.mark.skipif(not os.environ.get("CHORDCUE_MSI"), reason="MSI artifact not built")
def test_final_msi_policy() -> None:
    verify_msi(read_msi(Path(os.environ["CHORDCUE_MSI"])))


@pytest.mark.skipif(not os.environ.get("CHORDCUE_FROZEN_DIR"), reason="Frozen artifact not built")
def test_frozen_payload_complete() -> None:
    verify_tree(Path(os.environ["CHORDCUE_FROZEN_DIR"]))
