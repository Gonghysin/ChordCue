from chordcue.platform_events import (
    SessionGuard, WM_POWERBROADCAST, PBT_APMSUSPEND,
    WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK,
)
from PySide6.QtWidgets import QWidget


def test_lock_suspend_disarm_but_activation_does_not(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    calls = []
    guard = SessionGuard(window, lambda: calls.append("paused"))
    try:
        guard.handle_message(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK)
        guard.handle_message(WM_POWERBROADCAST, PBT_APMSUSPEND)
        guard.handle_message(WM_WTSSESSION_CHANGE, 8)  # Unlock must not start audio.
        guard.handle_message(6, 0)  # Losing focus must not interrupt desktop playback.
        assert calls == ["paused", "paused"]
    finally:
        guard.close()
        guard.close()
