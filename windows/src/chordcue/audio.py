"""Persistent local WebEngine metronome with a deliberately small native bridge."""

from __future__ import annotations

import json
import math
from pathlib import Path
import time
import uuid

from PySide6.QtCore import QFile, QIODevice, QObject, QStandardPaths, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .paths import resource_path


class _LocalResources(QWebEngineUrlRequestInterceptor):
    """Deny every request outside the immutable local resource allowlist."""

    def __init__(self, assets: set[Path], parent: QObject) -> None:
        super().__init__(parent)
        self._assets = assets

    def interceptRequest(self, info) -> None:
        url = info.requestUrl()
        allowed = url.isLocalFile() and Path(url.toLocalFile()).resolve() in self._assets
        info.block(not allowed)


class _LocalPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, document: Path, parent: QObject) -> None:
        super().__init__(profile, parent)
        self._document = document

    def acceptNavigationRequest(self, url: QUrl | str, kind, is_main_frame: bool) -> bool:
        url = QUrl(url)
        return (
            is_main_frame
            and url.isLocalFile()
            and Path(url.toLocalFile()).resolve() == self._document
        )

    def createWindow(self, kind):
        return None


class _Bridge(QObject):
    """No file, transport-control or network capabilities are exposed to JS."""

    def __init__(self, panel: AudioPanel) -> None:
        super().__init__(panel)
        self._panel = panel
        self._session = str(uuid.uuid4())

    @Slot(float, result="QVariantMap")
    def clock(self, started: float) -> dict:
        received = time.perf_counter_ns() / 1_000_000
        if not math.isfinite(started) or self._panel._closed:
            return {}
        return {
            "received": received,
            "sent": time.perf_counter_ns() / 1_000_000,
            "session": self._session,
        }

    @Slot(bool, int)
    def audioState(self, enabled: bool, sequence: int = 0) -> None:
        if self._panel._closed:
            return
        if sequence != getattr(self._panel, "_command_sequence", 0):
            return
        if self._panel._pending_enabled is not None:
            return
        self._panel._requested_enabled = enabled
        # A matching notification confirms JS has applied disable/cancel.
        # Release the emergency mute so a later explicit one-beat preview works.
        self._panel.page().setAudioMuted(False)
        if enabled != self._panel._enabled:
            self._panel._enabled = enabled
            self._panel.enabled_changed.emit(enabled)


def _bootstrap() -> str:
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Qt WebChannel's bundled JavaScript is unavailable")
    source = bytes(resource.readAll().data()).decode("utf-8")
    resource.close()
    return """window.ChordCueNative = true;
window.ChordCueHostLabel = '主机';
""" + source + """
(() => {
    let bridge = null;
    let audioSequence = 0;
    const pending = [];
    const clock = started => {
        if (!bridge) { pending.push(() => clock(started)); return; }
        bridge.clock(started, value => {
            if (value && Number.isFinite(value.received))
                window.acceptNativeClock?.(value, started);
        });
    };
    const audioState = value => {
        if (bridge) bridge.audioState(!!value, audioSequence);
        else pending.push(() => audioState(value));
    };
    const applyAudioCommand = (enabled, sequence) => {
        audioSequence = sequence;
        window.ChordCueMetronome.setEnabled(enabled);
    };
    window.ChordCueBridge = {clock, audioState, applyAudioCommand};
    new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.native;
        window.ChordCueBridgeReady = true;
        for (const call of pending.splice(0)) call();
    });
})();
"""


class AudioPanel(QWidget):
    """One WebEngine view remains alive and Active across tab/window changes.

    Audio enable commands are transient user actions, never restored preferences.
    Only metronome pattern, sound, volume and compensation live in localStorage.
    """

    enabled_changed = Signal(bool)
    ready_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ready = False
        self._closed = False
        self._enabled = False
        self._requested_enabled = False
        self._pending_enabled: bool | None = None
        self._command_sequence = 0
        self._document_epoch = 0
        self._pending: dict | None = None
        self._sending = False
        self._ready_attempts = 0
        document = resource_path("Broadcast.html").resolve()
        script = resource_path("Metronome.js").resolve()
        if not document.is_file() or not script.is_file():
            raise FileNotFoundError("ChordCue's bundled metronome resources are missing")

        data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        storage, cache = data / "WebEngine" / "Storage", data / "WebEngine" / "Cache"
        storage.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        # The profile must outlive its page, including deferred QWidget deletion.
        self._profile = QWebEngineProfile("ChordCueAudio", QApplication.instance())
        self._profile.setPersistentStoragePath(str(storage))
        self._profile.setCachePath(str(cache))
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        assets = {document, script}
        for name in ("score/ScoreIO.js", "score/ScoreView.js", "score/ScoreView.css",
                     "score/PlaybackPlan.js", "score/DeviceClient.js",
                     "vendor/alphatab/dist/alphaTab.min.js",
                     "vendor/alphatab/dist/font/Bravura.woff2"):
            path = document.parent / name
            if path.is_file():
                assets.add(path.resolve())
        self._requests = _LocalResources(assets, self._profile)
        self._profile.setUrlRequestInterceptor(self._requests)
        self.view = QWebEngineView(self)
        self._page = _LocalPage(self._profile, document, self.view)
        self.view.setPage(self._page)
        self._page.destroyed.connect(self._profile.deleteLater)
        settings = self._page.settings()
        for attribute, enabled in (
            (QWebEngineSettings.WebAttribute.LocalStorageEnabled, True),
            (QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False),
            (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
            (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True),
            (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
            (QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False),
            (QWebEngineSettings.WebAttribute.PluginsEnabled, False),
            (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False),
        ):
            settings.setAttribute(attribute, enabled)
        self._bridge = _Bridge(self)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("native", self._bridge)
        self._page.setWebChannel(self._channel)
        bootstrap = QWebEngineScript()
        bootstrap.setName("ChordCue restricted native bridge")
        bootstrap.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        bootstrap.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        bootstrap.setRunsOnSubFrames(False)
        bootstrap.setSourceCode(_bootstrap())
        self._page.scripts().insert(bootstrap)
        self._page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        self._page.recommendedStateChanged.connect(self._keep_active)
        self._page.loadStarted.connect(self._loading)
        self._page.loadFinished.connect(self._loaded)
        self._page.renderProcessTerminated.connect(self._renderer_stopped)
        self._page.permissionRequested.connect(lambda permission: permission.deny())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.load(QUrl.fromLocalFile(str(document)))

    def page(self) -> QWebEnginePage:
        return self._page

    def _keep_active(self, *_args) -> None:
        if not self._closed:
            self._page.setLifecycleState(QWebEnginePage.LifecycleState.Active)

    def _loading(self) -> None:
        self._ready_attempts = 0
        self._renderer_stopped()

    def _loaded(self, successful: bool) -> None:
        if self._closed:
            return
        if not successful:
            self._renderer_stopped()
            return
        self._page.runJavaScript(
            "!!(window.ChordCueBridgeReady && window.ChordCueMetronome && window.acceptNativeState)",
            lambda result, epoch=self._document_epoch: self._confirmed_ready(result, epoch),
        )

    def _confirmed_ready(self, successful, epoch: int) -> None:
        if self._closed or epoch != self._document_epoch:
            return
        self.ready = bool(successful)
        if self.ready:
            self.ready_changed.emit(True)
            self._flush()
        elif self._ready_attempts < 50:
            self._ready_attempts += 1
            QTimer.singleShot(100, lambda: self._loaded(True) if epoch == self._document_epoch else None)
        else:
            self.ready_changed.emit(False)

    def _renderer_stopped(self, *_args) -> None:
        if self._closed:
            return
        self._document_epoch += 1
        self._sent_route_id = None
        self._command_sequence += 1
        self.ready = False
        self.ready_changed.emit(False)
        # Every fresh renderer starts disarmed and receives the current command
        # sequence before its own button notifications can change native state.
        self._pending_enabled = False
        self._requested_enabled = False
        self._sending = False
        self._page.setAudioMuted(True)
        if self._enabled:
            self._enabled = False
            self.enabled_changed.emit(False)

    def update_transport(self, payload: dict, name: str = "") -> None:
        if self._closed:
            return
        # Copy across the asynchronous boundary and forbid NaN/Infinity in JS.
        wire = payload
        route_id = payload.get("routeId")
        if route_id and route_id == getattr(self, "_sent_route_id", None):
            wire = {key: value for key, value in payload.items() if key != "route"}
        self._pending = json.loads(json.dumps({"name": name, "transport": wire}, allow_nan=False))
        self._flush()

    def set_enabled(self, enabled: bool) -> None:
        if self._closed:
            return
        enabled = bool(enabled)
        if enabled and enabled == self._requested_enabled:
            return
        self._command_sequence += 1
        self._page.setAudioMuted(not enabled)
        self._requested_enabled = enabled
        self._pending_enabled = enabled
        self._flush()

    def _flush(self) -> None:
        if not self.ready or self._closed or self._sending:
            return
        value, enabled = self._pending, self._pending_enabled
        if value is None and enabled is None:
            return
        self._pending = None
        self._pending_enabled = None
        calls = []
        if value is not None:
            calls.append("window.acceptNativeState(" + json.dumps(value, ensure_ascii=True) + ");")
            if "route" in value["transport"]:
                self._sent_route_id = value["transport"].get("routeId")
        if enabled is not None:
            calls.append("window.ChordCueBridge.applyAudioCommand(" + json.dumps(enabled)
                         + "," + str(self._command_sequence) + ");")
        self._sending = True
        self._page.runJavaScript("\n".join(calls),
                                 lambda result, epoch=self._document_epoch: self._sent(result, epoch))

    def _sent(self, _result, epoch: int) -> None:
        if self._closed or epoch != self._document_epoch:
            return
        self._sending = False
        self._flush()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.ready = False
        self._pending = None
        self._pending_enabled = None
        self._page.setAudioMuted(True)
        self._page.runJavaScript("window.ChordCueMetronome?.shutdown();")
        self._page.setWebChannel(None)  # type: ignore[arg-type]  # Qt accepts nullptr.
        self._channel.deregisterObject(self._bridge)
        if self._enabled:
            self._enabled = False
            self.enabled_changed.emit(False)
        self.ready_changed.emit(False)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
