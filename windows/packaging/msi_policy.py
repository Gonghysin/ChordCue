"""Installer policy shared by the builder and final-artifact verification."""

UPGRADE_CODE = "{59A75696-D601-43F8-B523-985CF1C78E60}"
PRODUCT_NAME = "ChordCue"
INSTALL_DIRECTORY = r"[ProgramFiles64Folder]\ChordCue"
DOWNGRADE_CONDITION = "Installed OR NOT NEWERVERSIONFOUND"
WINDOWS_CONDITION = "Installed OR (VersionNT64 AND WINDOWSBUILD >= 19045)"
REMOVE_EXISTING_SEQUENCE = 1501


def upgrade_rows(version: str) -> list[tuple]:
    # Version bounds are exclusive. OnlyDetect (2) is essential: a newer
    # product must NEVER become a RemoveExistingProducts target.
    return [
        (UPGRADE_CODE, None, version, None, 1, None, "OLDERVERSIONFOUND"),
        (UPGRADE_CODE, version, None, None, 2, None, "NEWERVERSIONFOUND"),
    ]
