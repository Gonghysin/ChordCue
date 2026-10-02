"""Locate immutable bundled assets and writable user data separately."""

from pathlib import Path
import sys


def resource_path(name: str) -> Path:
    """Return an asset from the frozen payload or source checkout."""
    if Path(name).name != name:
        raise ValueError("Asset names must not contain directory components")
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent / "Resources"
    else:
        root = Path(__file__).resolve().parents[3] / "Resources"
    return root / name
