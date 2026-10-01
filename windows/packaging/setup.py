"""Run through windows/scripts/build.ps1 using the locked Windows venv."""
from __future__ import annotations

import os
import struct
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid5

from cx_Freeze import Executable, setup
from cx_Freeze.command.bdist_msi import bdist_msi
from msilib import add_data

from msi_policy import (
    DOWNGRADE_CONDITION,
    INSTALL_DIRECTORY,
    PRODUCT_NAME,
    REMOVE_EXISTING_SEQUENCE,
    UPGRADE_CODE,
    WINDOWS_CONDITION,
    upgrade_rows,
)

HERE = Path(__file__).resolve().parent
WINDOWS = HERE.parent
ROOT = WINDOWS.parent
os.chdir(HERE)  # Do not let setuptools apply the application's pyproject twice.
sys.path.insert(0, str(WINDOWS / "src"))
if sys.version_info[:2] != (3, 13) or struct.calcsize("P") != 8:
    raise RuntimeError("Build requires CPython 3.13 x64")
if version("cx-Freeze") != "8.7.1":
    raise RuntimeError("Use the locked cx-Freeze 8.7.1 build environment")

APP_VERSION = tomllib.loads((WINDOWS / "pyproject.toml").read_text("utf-8"))["project"]["version"]
PROTOTYPE = os.environ.get("CHORDCUE_BUILD_PROTOTYPE") == "1"
BUILD = WINDOWS / ("build-prototype" if PROTOTYPE else "build")
DIST = WINDOWS / ("dist-prototype" if PROTOTYPE else "dist")
LICENSE_STAGE = BUILD / "licenses"


class ChordCueMSI(bdist_msi):
    """Keep changes at MSI generation time, then audit the actual database."""

    def sql(self, query: str) -> None:
        view = self.db.OpenView(query)
        view.Execute(None)
        view.Close()

    def add_properties(self) -> None:
        super().add_properties()
        self.sql("UPDATE `Property` SET `Value` = '1' WHERE `Property` = 'ALLUSERS'")

    def add_config(self) -> None:
        super().add_config()
        self.sql(
            "UPDATE `Property` SET `Value` = "
            "'TARGETDIR;REINSTALLMODE;OLDERVERSIONFOUND;NEWERVERSIONFOUND' "
            "WHERE `Property` = 'SecureCustomProperties'"
        )
        # cx_Freeze's default 1450 is BEFORE InstallInitialize (1500), so it
        # cannot restore the old product if the replacement fails.
        self.sql(
            "UPDATE `InstallExecuteSequence` SET `Sequence` = "
            f"{REMOVE_EXISTING_SEQUENCE} WHERE `Action` = 'RemoveExistingProducts'"
        )
        # msilib defaults run these at 200/400, after LaunchConditions (100),
        # which silently defeats downgrade checks and the OS-build search.
        for table in ("InstallUISequence", "InstallExecuteSequence"):
            self.sql(f"UPDATE `{table}` SET `Sequence` = 25 WHERE `Action` = 'FindRelatedProducts'")
            self.sql(f"UPDATE `{table}` SET `Sequence` = 50 WHERE `Action` = 'AppSearch'")
        # VersionNT64 reports 603 even on Windows 10. Read the actual build
        # using standard MSI AppSearch (no executable/custom registry action).
        add_data(self.db, "RegLocator", [
            ("WindowsBuild", 2, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuildNumber", 18),
        ])
        add_data(self.db, "AppSearch", [("WINDOWSBUILD", "WindowsBuild")])
        add_data(self.db, "LaunchCondition", [
            (DOWNGRADE_CONDITION, "A newer version of ChordCue is already installed."),
            (WINDOWS_CONDITION, "ChordCue requires 64-bit Windows 10 22H2 (build 19045) or later."),
            ("ALLUSERS = 1", "ChordCue must be installed for all users."),
        ])

    def add_upgrade_config(self, sversion: str) -> None:
        add_data(self.db, "Upgrade", upgrade_rows(sversion))


def resource_files() -> list[tuple[str, str]]:
    if not LICENSE_STAGE.is_dir():
        raise RuntimeError("Run collect_licenses.py before building")
    return [
        (str(ROOT / "Resources"), "Resources"),
        (str(ROOT / "LICENSE"), "Resources/LICENSE"),
        (str(ROOT / "THIRD_PARTY_NOTICES.md"), "Resources/THIRD_PARTY_NOTICES.md"),
        (str(LICENSE_STAGE), "Resources/licenses"),
    ]


setup(
    name=PRODUCT_NAME,
    version=APP_VERSION,
    description="Standalone chord chart and metronome",
    author="ChordCue contributors",
    url="https://github.com/Gonghysin/ChordCue",
    cmdclass={"bdist_msi": ChordCueMSI},
    executables=[Executable(
        str(HERE / "smoke_app.py" if PROTOTYPE else WINDOWS / "src/chordcue/main.py"),
        base="console" if PROTOTYPE else "gui",
        target_name="ChordCueProbe.exe" if PROTOTYPE else "ChordCue.exe",
        icon=str(BUILD / "ChordCue.ico"),
        shortcut_name=None if PROTOTYPE else "ChordCue",
        shortcut_dir=None if PROTOTYPE else "ProgramMenuFolder",
    )],
    options={
        "build_exe": {
            "build_exe": str(BUILD / "exe"),
            "include_files": resource_files(),
            "include_msvcr": True,
            # Qt 6.11 uses Windows' system ICU. Do not pick an unrelated
            # icuuc.dll from developer PATH (for example conda/Poppler ICU).
            "bin_excludes": ["icuuc.dll"],
            "includes": [
                "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
                "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                "PySide6.QtWebChannel", "PySide6.QtPrintSupport",
            ],
            "packages": [] if PROTOTYPE else ["chordcue"],
            "excludes": ["tkinter", "unittest", "pytest", "pip", "setuptools"],
            "zip_include_packages": ["encodings"],
            "zip_exclude_packages": ["*"],
        },
        "bdist_msi": {
            "bdist_dir": str(BUILD / "msi"),
            "dist_dir": str(DIST),
            "output_name": f"ChordCue-{APP_VERSION}-win-x64.msi",
            "all_users": True,
            "add_to_path": False,
            "launch_on_finish": False,
            "install_icon": str(BUILD / "ChordCue.ico"),
            "initial_target_dir": INSTALL_DIRECTORY,
            "upgrade_code": UPGRADE_CODE,
            "product_code": "{" + str(uuid5(UUID(UPGRADE_CODE.strip("{}")), APP_VERSION)).upper() + "}",
            "directories": [("ProgramMenuFolder", "TARGETDIR", ".")],
        },
    },
)
