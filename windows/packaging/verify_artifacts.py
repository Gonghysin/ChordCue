"""Audit the finished MSI and frozen tree, not just setup.py's intent."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from msi_policy import DOWNGRADE_CONDITION, INSTALL_DIRECTORY, UPGRADE_CODE, WINDOWS_CONDITION

TABLES = {
    "Property": ["Property", "Value"],
    "Upgrade": ["UpgradeCode", "VersionMin", "VersionMax", "Language", "Attributes", "Remove", "ActionProperty"],
    "InstallExecuteSequence": ["Action", "Condition", "Sequence"],
    "InstallUISequence": ["Action", "Condition", "Sequence"],
    "LaunchCondition": ["Condition", "Description"],
    "CustomAction": ["Action", "Type", "Source", "Target"],
    "Directory": ["Directory", "Directory_Parent", "DefaultDir"],
    "Component": ["Component", "ComponentId", "Directory_", "Attributes", "Condition", "KeyPath"],
    "File": ["File", "Component_", "FileName", "FileSize", "Version", "Language", "Attributes", "Sequence"],
    "Environment": ["Environment", "Name", "Value", "Component_"],
    "Registry": ["Registry", "Root", "Key", "Name", "Value", "Component_"],
    "RemoveFile": ["FileKey", "Component_", "FileName", "DirProperty", "InstallMode"],
    "ControlEvent": ["Dialog_", "Control_", "Event", "Argument", "Condition", "Ordering"],
    "AppSearch": ["Property", "Signature_"],
    "RegLocator": ["Signature_", "Root", "Key", "Name", "Type"],
    "Shortcut": ["Shortcut", "Directory_", "Name", "Component_", "Target", "Arguments", "Icon_"],
}


def read_msi(path: Path) -> dict:
    from msilib import MSIDBOPEN_READONLY, OpenDatabase
    db = OpenDatabase(str(path.resolve()), MSIDBOPEN_READONLY)
    result = {}
    for table, columns in TABLES.items():
        view = db.OpenView(f"SELECT {', '.join('`' + x + '`' for x in columns)} FROM `{table}`")
        view.Execute(None)
        rows = []
        while (record := view.Fetch()) is not None:
            rows.append({name: record.GetString(i + 1) for i, name in enumerate(columns)})
        view.Close()
        result[table] = rows
    template = db.GetSummaryInformation(0).GetProperty(7)
    result["Summary"] = {"Template": template.decode("ascii") if isinstance(template, bytes) else template}
    del db
    return result


def verify_msi(tables: dict) -> None:
    properties = {row["Property"]: row["Value"] for row in tables["Property"]}
    assert properties["ALLUSERS"] == "1", "MSI must install per-machine"
    assert "MSIINSTALLPERUSER" not in properties
    assert properties["UpgradeCode"] == UPGRADE_CODE
    assert properties["ARPPRODUCTICON"] == "InstallIcon"
    assert any(row["Directory_"] == "ProgramMenuFolder" and row["Name"] == "ChordCue"
               for row in tables["Shortcut"]), "Missing Start menu shortcut"
    assert any(row["FileName"].split("|")[-1] == "ChordCue.exe" for row in tables["File"])
    assert str(tables["Summary"]["Template"]).lower().startswith("x64"), "MSI must be x64"
    assert all(int(row["Attributes"]) & 256 for row in tables["Component"]), "Components must be 64-bit"
    assert {"OLDERVERSIONFOUND", "NEWERVERSIONFOUND"} <= set(properties["SecureCustomProperties"].split(";"))
    upgrades = {row["ActionProperty"]: row for row in tables["Upgrade"]}
    assert set(upgrades) == {"OLDERVERSIONFOUND", "NEWERVERSIONFOUND"}
    older, newer = upgrades["OLDERVERSIONFOUND"], upgrades["NEWERVERSIONFOUND"]
    assert older["UpgradeCode"] == newer["UpgradeCode"] == UPGRADE_CODE
    assert older["VersionMax"] == newer["VersionMin"] == properties["ProductVersion"]
    assert int(older["Attributes"]) == 1 and int(newer["Attributes"]) == 2
    assert not older["VersionMin"] and not newer["VersionMax"]
    conditions = {row["Condition"] for row in tables["LaunchCondition"]}
    assert DOWNGRADE_CONDITION in conditions
    assert "ALLUSERS = 1" in conditions
    assert WINDOWS_CONDITION in conditions
    assert tables["AppSearch"] == [{"Property": "WINDOWSBUILD", "Signature_": "WindowsBuild"}]
    assert tables["RegLocator"] == [{
        "Signature_": "WindowsBuild", "Root": "2",
        "Key": r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        "Name": "CurrentBuildNumber", "Type": "18",
    }]
    execute = {row["Action"]: int(row["Sequence"]) for row in tables["InstallExecuteSequence"]}
    assert execute["InstallInitialize"] < execute["RemoveExistingProducts"] < execute["ProcessComponents"]
    for table in ["InstallExecuteSequence", "InstallUISequence"]:
        sequence = {row["Action"]: int(row["Sequence"]) for row in tables[table]}
        assert sequence["FindRelatedProducts"] < sequence["LaunchConditions"]
        assert sequence["AppSearch"] < sequence["LaunchConditions"]
    custom = {row["Action"]: row for row in tables["CustomAction"]}
    assert set(custom) == {"A_SET_TARGET_DIR", "A_SET_REINSTALL_MODE"}, "Unexpected custom action"
    assert custom["A_SET_TARGET_DIR"]["Target"] == INSTALL_DIRECTORY
    assert all(int(row["Type"]) == 307 for row in custom.values())
    assert not tables["Environment"], "Installer must not modify PATH or environment"
    assert not tables["Registry"], "No application/firewall registry customizations"
    assert not tables["RemoveFile"], "Never remove user projects or AppData"
    assert all(row["Event"] != "DoAction" for row in tables["ControlEvent"])
    assert not any("appdata" in str(row).lower() for row in tables["Directory"])
    verify_payload([row["FileName"].split("|")[-1] for row in tables["File"]])


def verify_payload(names: list[str]) -> None:
    lowered = {name.lower() for name in names}
    for required in [
        "QtWebEngineProcess.exe", "Qt6WebEngineCore.dll", "qwindows.dll", "icudtl.dat",
        "qtwebengine_resources.pak", "qtwebengine_devtools_resources.pak", "en-US.pak",
        "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
        "Broadcast.html", "Metronome.js", "DemoChords.txt", "THIRD_PARTY_NOTICES.md",
        "Python-LICENSE.txt", "installed-distributions.json",
        "alphaTab-integrated-NOTICES.txt",
        "Qt-LGPL-3.0.txt", "Qt-GPL-3.0.txt", "GNU-LGPL-2.1.txt",
        "Qt-6.11.2-THIRD-PARTY-NOTICES.txt", "Qt-6.11.2-notices-manifest.json",
        "PySide6-6.11.2-NOTICES.txt", "Qt-PySide-SOURCE-INFORMATION.txt",
    ]:
        assert required.lower() in lowered, f"Missing frozen payload: {required}"


def verify_tree(directory: Path) -> None:
    files = [path for path in directory.rglob("*") if path.is_file()]
    verify_payload([path.name for path in files])
    assert (directory / "Resources/licenses/installed-distributions.json").is_file()
    assert (directory / "Resources/licenses/alphaTab-integrated-NOTICES.txt").is_file()
    for relative in ("score/ScoreView.html", "score/ScoreView.js", "score/ScoreView.css",
                     "score/ScoreIO.js", "score/MusicXMLImport.js", "score/GuitarProImport.js",
                     "score/PlaybackPlan.js", "score/DeviceClient.js",
                     "score/score.schema.json",
                     "vendor/alphatab/dist/alphaTab.min.js", "vendor/alphatab/LICENSE.header",
                     "vendor/alphatab/dist/font/Bravura.woff2", "vendor/fflate/umd/index.js"):
        assert (directory / "Resources" / relative).is_file(), f"Missing score resource: {relative}"
    for package in ("alphatab", "fflate"):
        root = directory / "Resources/vendor" / package
        vendor = json.loads((root / "manifest.json").read_text("utf-8"))
        for entry in vendor["files"]:
            assert hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest() == entry["sha256"], entry["path"]
    assert any("platforms" in path.parts and path.name == "qwindows.dll" for path in files)
    assert any("qtwebengine_locales" in path.parts and path.name == "en-US.pak" for path in files)
    assert (directory / "share/licenses/vc_redist/LICENSE.txt").is_file()
    assert not any(path.name.lower() == "icuuc.dll" for path in files), "Do not bundle non-system ICU"
    manifest = json.loads((directory / "Resources/licenses/installed-distributions.json").read_text("utf-8"))
    missing = [row["name"] for row in manifest if not row["licenses"]]
    assert not missing, f"No license text collected for: {missing}"
    notices = json.loads((directory / "Resources/licenses/Qt-6.11.2-notices-manifest.json").read_text("utf-8"))
    for entry in [notices["aggregate"], notices["pysideSourceNotices"]["aggregate"],
                  *notices["supplementalFullTexts"]]:
        payload = (directory / "Resources/licenses" / entry["file"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"], f"License hash mismatch: {entry['file']}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--msi", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.tree and not args.msi:
        parser.error("specify --tree and/or --msi")
    if args.tree:
        verify_tree(args.tree)
    if args.msi:
        tables = read_msi(args.msi)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(tables, indent=2), "utf-8")
        verify_msi(tables)
    print("Packaging artifact checks passed")
