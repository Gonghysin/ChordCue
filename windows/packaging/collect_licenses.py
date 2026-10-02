"""Collect installed license texts without copying developer tools into the app."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / "licenses").glob("*"):
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    pending = ["PySide6", "cx-Freeze", "freeze-core"]
    seen: set[str] = set()
    manifest = []
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        dist = distribution(name)
        texts = []
        for item in dist.files or ():
            # Wheel metadata license paths and PySide's full Qt license tree.
            parts = [part.lower() for part in item.parts]
            if any(p in {"licenses", "license", "copying"} for p in parts) or (
                item.name.lower().startswith(("license", "copying", "copyright"))
                and not item.name.lower().endswith((".py", ".pyc", ".pyd"))
            ):
                source = Path(dist.locate_file(item))
                if source.is_file() and ".." not in item.parts:
                    target = destination / name / Path(*item.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    texts.append(target.relative_to(destination).as_posix())
        manifest.append({"name": dist.metadata["Name"], "version": dist.version, "licenses": texts})
        # cx_Freeze startup code and freeze-core's executable base are shipped;
        # their build-time tooling dependencies are not application dependencies.
        requirements = [] if name in {"cx-freeze", "freeze-core"} else (dist.requires or ())
        for raw in requirements:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                pending.append(req.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Python license not found: {python_license}")
    shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    (destination / "installed-distributions.json").write_text(
        json.dumps(sorted(manifest, key=lambda row: row["name"]), indent=2), "utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    collect(parser.parse_args().destination)
