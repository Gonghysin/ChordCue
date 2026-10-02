"""Fetch a pinned alphaTab package without executing package install scripts."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.8.4"
URL = f"https://registry.npmjs.org/@coderline/alphatab/-/alphatab-{VERSION}.tgz"
INTEGRITY = "VN5rfTZZWgA63Ny1aDKCp02k3Qm9CHhg4Q9AnK0kHm7G+fNDNZo36TeToPDFoJ6VpB9+AHcCrHwHFUP1tKqdsw=="


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as response:
        package = response.read(20_000_001)
    if len(package) > 20_000_000:
        raise ValueError("package exceeds download limit")
    if base64.b64encode(hashlib.sha512(package).digest()).decode() != INTEGRITY:
        raise ValueError("npm package integrity mismatch")
    destination = ROOT / "Resources" / "vendor" / "alphatab"
    records = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name
            if not member.isfile():
                continue
            if not (name.startswith("package/dist/") or name in ("package/LICENSE", "package/LICENSE.header", "package/package.json")):
                continue
            relative = Path(name.removeprefix("package/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe package path")
            if member.size > 12_000_000 or total + member.size > 25_000_000:
                raise ValueError("package exceeds unpack limit")
            total += member.size
            if relative.suffix in (".map", ".ts") or "soundfont" in name.lower():
                continue
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("missing package member")
            data = source.read(member.size + 1)
            if len(data) != member.size:
                raise ValueError("invalid package member size")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            records.append({"path": relative.as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"package": "@coderline/alphatab", "version": VERSION, "license": "MPL-2.0", "url": URL, "integrity": "sha512-" + INTEGRITY, "files": records}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"version": VERSION, "files": len(records), "bytes": sum(item["bytes"] for item in records)}))
    zip_url = "https://registry.npmjs.org/fflate/-/fflate-0.8.2.tgz"
    zip_integrity = "cPJU47OaAoCbg0pBvzsgpTPhmhqI5eJjh/JIu8tPj5q+T7iLvW/JAYUqmE7KOB4R1ZyEhzBaIQpQpardBF5z8A=="
    with urllib.request.urlopen(zip_url, timeout=60) as response:
        zip_package = response.read(2_000_001)
    if len(zip_package) > 2_000_000 or base64.b64encode(hashlib.sha512(zip_package).digest()).decode() != zip_integrity:
        raise ValueError("fflate package integrity mismatch")
    zip_destination = ROOT / "Resources" / "vendor" / "fflate"
    zip_records = []
    with tarfile.open(fileobj=io.BytesIO(zip_package), mode="r:gz") as archive:
        for name in ("package/umd/index.js", "package/LICENSE", "package/package.json"):
            source = archive.extractfile(name)
            if source is None:
                raise ValueError("missing fflate member")
            data = source.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("fflate member too large")
            relative = Path(name.removeprefix("package/"))
            target = zip_destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            zip_records.append({"path": relative.as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (zip_destination / "manifest.json").write_text(json.dumps({"package": "fflate", "version": "0.8.2", "license": "MIT", "url": zip_url, "integrity": "sha512-" + zip_integrity, "files": zip_records}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
