"""Per-user UI preferences; projects contain no machine-specific state."""
from pathlib import Path

from PySide6.QtCore import QSettings


class Settings:
    def __init__(self, path: str | Path | None = None):
        self.store = (QSettings(str(path), QSettings.Format.IniFormat) if path else
                      QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                                "ChordCue", "ChordCue"))

    def value(self, key, default=None):
        return self.store.value(key, default)

    def set_value(self, key, value):
        self.store.setValue(key, value)

    def sync(self):
        self.store.sync()
