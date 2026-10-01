"""Pause and disarm audio when a Windows session locks or suspends."""

from collections.abc import Callable
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication, QWidget

WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMSUSPEND = 0x0004
WTS_SESSION_LOCK = 0x0007


class SessionGuard(QAbstractNativeEventFilter):
    """Own the native notification registration for one top-level window."""

    def __init__(self, window: QWidget, suspend: Callable[[], None]) -> None:
        super().__init__()
        self._suspend = suspend
        self._registered = False
        self._hwnd = int(window.winId())
        self._wts = None
        app = QApplication.instance()
        if sys.platform == "win32" and app is not None:
            self._wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
            self._wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            self._wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
            self._wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            self._wts.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
            self._registered = bool(self._wts.WTSRegisterSessionNotification(self._hwnd, 0))
            app.installNativeEventFilter(self)

    def handle_message(self, message: int, parameter: int) -> None:
        if (message, parameter) in {
            (WM_POWERBROADCAST, PBT_APMSUSPEND),
            (WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK),
        }:
            self._suspend()

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) in {
            b"windows_generic_MSG", b"windows_dispatcher_MSG"
        }:
            native = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            self.handle_message(native.message, native.wParam)
        return False, 0

    def close(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)
        if self._registered and self._wts is not None:
            self._wts.WTSUnRegisterSessionNotification(self._hwnd)
            self._registered = False
