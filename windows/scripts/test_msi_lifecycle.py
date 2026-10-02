"""Explicit, elevated disposable-machine MSI lifecycle test. Never auto-elevates.

Requires two real release versions. Modifies Program Files and machine MSI state;
do not run on a user's working installation. Diagnostic logs are retained.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
from msi_policy import UPGRADE_CODE  # noqa: E402
from verify_artifacts import read_msi, verify_msi  # noqa: E402


def related_products() -> set[str]:
    products = set()
    for index in range(100):
        product = ctypes.create_unicode_buffer(39)
        code = ctypes.windll.msi.MsiEnumRelatedProductsW(UPGRADE_CODE, 0, index, product)
        if code == 259:  # ERROR_NO_MORE_ITEMS
            return products
        if code:
            raise OSError(code, "MsiEnumRelatedProductsW failed")
        products.add(product.value)
    raise RuntimeError("Unexpected number of related products")


def properties(path: Path) -> dict[str, str]:
    tables = read_msi(path)
    verify_msi(tables)
    return {row["Property"]: row["Value"] for row in tables["Property"]}


def make_failure_probe(source: Path, destination: Path) -> None:
    """Separate test-only package that fails inside the upgrade transaction."""
    from msilib import MSIDBOPEN_TRANSACT, OpenDatabase, add_data
    shutil.copy2(source, destination)
    db = OpenDatabase(str(destination), MSIDBOPEN_TRANSACT)
    add_data(db, "CustomAction", [("ChordCueTestFailure", 19, None, "Intentional rollback test")])
    add_data(db, "InstallExecuteSequence", [("ChordCueTestFailure", "NOT Installed", 6501)])
    info = db.GetSummaryInformation(1)
    info.SetProperty(9, "{" + str(uuid4()).upper() + "}")  # distinct test PackageCode
    info.Persist()
    db.Commit()
    del db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("older_msi", type=Path)
    parser.add_argument("newer_msi", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-machine-changes", action="store_true")
    args = parser.parse_args()
    if not args.allow_machine_changes:
        parser.error("Explicit --allow-machine-changes is required on a disposable test machine")
    if sys.platform != "win32" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Run in an already-elevated Windows terminal; this script never requests UAC")
    if related_products():
        raise RuntimeError("Refusing to modify an existing ChordCue installation")
    installed_exe = Path(os.environ["ProgramFiles"]) / "ChordCue/ChordCue.exe"
    if installed_exe.parent.exists():
        raise RuntimeError("Refusing to modify an existing Program Files/ChordCue directory")
    older, newer = properties(args.older_msi), properties(args.newer_msi)
    if tuple(map(int, older["ProductVersion"].split("."))) >= tuple(map(int, newer["ProductVersion"].split("."))):
        raise ValueError("Two increasing ProductVersions are required")
    if older["ProductCode"] == newer["ProductCode"]:
        raise ValueError("Different versions must have different ProductCodes")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    probe = output / "TEST-ONLY-DO-NOT-DISTRIBUTE-rollback.msi"
    make_failure_probe(args.newer_msi.resolve(), probe)
    report: dict = {"older": older["ProductVersion"], "newer": newer["ProductVersion"], "steps": []}
    sentinel = Path(os.environ["LOCALAPPDATA"]) / "ChordCue" / f"msi-test-{uuid4()}.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("Preserve user data during repair, upgrade, rollback and uninstall.", "utf-8")

    def run(name: str, *arguments: str, success: bool = True) -> None:
        code = subprocess.run([
            "msiexec.exe", *arguments, "/qn", "/norestart", "/L*v", str(output / f"{name}.log"),
        ], check=False, timeout=300).returncode
        report["steps"].append({"name": name, "exit_code": code})
        if success and code not in (0, 3010):
            raise AssertionError(f"{name}: MSI exit {code}")
        if not success and code != 1603:
            raise AssertionError(f"{name}: expected blocked/failing MSI exit 1603, got {code}")

    try:
        run("01-install-old", "/i", str(args.older_msi.resolve()))
        assert related_products() == {older["ProductCode"]}
        original_digest = hashlib.sha256(installed_exe.read_bytes()).hexdigest()
        run("02-failed-upgrade", "/i", str(probe), success=False)
        assert related_products() == {older["ProductCode"]}, "Rollback did not restore old registration"
        assert hashlib.sha256(installed_exe.read_bytes()).hexdigest() == original_digest
        run("03-upgrade", "/i", str(args.newer_msi.resolve()))
        assert related_products() == {newer["ProductCode"]}
        current_digest = hashlib.sha256(installed_exe.read_bytes()).hexdigest()
        # Delete only the executable installed by this test, then prove repair.
        installed_exe.unlink()
        run("04-repair", "/fa", newer["ProductCode"])
        assert hashlib.sha256(installed_exe.read_bytes()).hexdigest() == current_digest
        run("05-downgrade-block", "/i", str(args.older_msi.resolve()), success=False)
        assert related_products() == {newer["ProductCode"]}
        assert hashlib.sha256(installed_exe.read_bytes()).hexdigest() == current_digest
        run("06-uninstall", "/x", newer["ProductCode"])
        assert not related_products() and not installed_exe.exists()
        assert sentinel.is_file(), "Installer removed user AppData"
        report["passed"] = True
    finally:
        # On failure remove only products introduced by this clean-machine test.
        for product in related_products() & {older["ProductCode"], newer["ProductCode"]}:
            run("cleanup-" + product.strip("{}"), "/x", product)
        report["appdata_preserved"] = sentinel.is_file()
        (output / "lifecycle.json").write_text(json.dumps(report, indent=2), "utf-8")
        sentinel.unlink(missing_ok=True)  # Remove only our unique test sentinel.
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
