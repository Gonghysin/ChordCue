"""Offline notation/import view with no JavaScript file or network privileges."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import cast

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .audio import _LocalPage, _LocalResources
from .paths import resource_path
from .score_models import MAX_SCORE_BYTES, ScoreIR


class _SeekBridge(QObject):
    @Slot(str, float)
    def seek(self, measure_id, offset):
        panel = cast("ScorePanel", self.parent())
        if panel._closed or panel._latest is None:
            return
        measure = next((m for m in panel._latest[0].measures if m.id == measure_id), None)
        if measure and 0 <= offset < float(measure.duration.as_fraction()):
            panel.seek_requested.emit(measure_id, offset)


class ScorePanel(QWidget):
    ready_changed = Signal(bool)
    seek_requested = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ready = False
        self._closed = False
        self._epoch = 0
        self._pending = None
        self._latest = None
        self._display_error = None
        self._last_sample = None
        self._follow = True
        self._active = True
        root = resource_path("Broadcast.html").parent
        document = root / "score/ScoreView.html"
        names = ("score/ScoreView.html", "score/ScoreView.css", "score/ScoreView.js",
                 "score/ScoreIO.js", "score/MusicXMLImport.js", "score/GuitarProImport.js",
                 "vendor/alphatab/dist/alphaTab.min.js", "vendor/fflate/umd/index.js",
                 "vendor/alphatab/dist/font/Bravura.woff2")
        self._profile = QWebEngineProfile(QApplication.instance())
        self._requests = _LocalResources({(root / name).resolve() for name in names}, self._profile)
        self._profile.setUrlRequestInterceptor(self._requests)
        self.view = QWebEngineView(self)
        self._page = _LocalPage(self._profile, document.resolve(), self.view)
        self.view.setPage(self._page)
        self._bridge = _SeekBridge(self)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("scoreSeek", self._bridge)
        self._page.setWebChannel(self._channel)
        channel = QFile(":/qtwebchannel/qwebchannel.js")
        if not channel.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Qt WebChannel resource unavailable")
        source = bytes(channel.readAll().data()).decode("utf-8")
        channel.close()
        bootstrap = QWebEngineScript()
        bootstrap.setName("ChordCue score seek")
        bootstrap.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        bootstrap.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        bootstrap.setSourceCode(source + "\nnew QWebChannel(qt.webChannelTransport,c=>window.ChordCueScoreBridge=c.objects.scoreSeek);")
        self._page.scripts().insert(bootstrap)
        self._page.destroyed.connect(self._profile.deleteLater)
        for attribute, enabled in (
            (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
            (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True),
            (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
            (QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False),
            (QWebEngineSettings.WebAttribute.PluginsEnabled, False),
        ):
            self._page.settings().setAttribute(attribute, enabled)
        self._page.permissionRequested.connect(lambda permission: permission.deny())
        self._page.loadFinished.connect(self._loaded)
        self._page.renderProcessTerminated.connect(lambda *_: self._unavailable())
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._import_timeout)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.load(QUrl.fromLocalFile(str(document)))

    def _unavailable(self):
        self.ready = False
        self.ready_changed.emit(False)

    def _loaded(self, successful):
        if self._closed:
            return
        if not successful:
            self._unavailable()
            return
        self._page.runJavaScript("window.ScoreViewReady===true", self._confirmed)

    def _confirmed(self, ready):
        if self._closed:
            return
        self.ready = bool(ready)
        self.ready_changed.emit(self.ready)
        if self.ready:
            self.set_follow(self._follow)
            self.set_active(self._active)
            if self._latest:
                self.show_score(*self._latest)
            elif self._display_error is not None:
                self.clear_score(self._display_error)
            if self._pending:
                path, success, failure = self._pending
                self._pending = None
                self.import_file(path, success, failure)

    def import_file(self, path, success, failure):
        if self._closed:
            failure("谱面视图已关闭")
            return
        if not self.ready:
            self._pending = (path, success, failure)
            return
        self._epoch += 1
        epoch = self._epoch
        self._failure = failure
        try:
            path = Path(path)
            if path.stat().st_size > MAX_SCORE_BYTES:
                raise ValueError("谱面文件超过 32 MiB 限制")
            data = path.read_bytes()
            if len(data) > MAX_SCORE_BYTES:
                raise ValueError("谱面文件超过 32 MiB 限制")
            arguments = [base64.b64encode(data).decode("ascii"), path.name,
                         hashlib.sha256(data).hexdigest()]
        except (OSError, ValueError) as error:
            failure(str(error))
            return
        script = "(()=>{try{return JSON.stringify({ok:true,score:window.importScoreBytes(" + ",".join(
            json.dumps(item, ensure_ascii=True) for item in arguments) + ")})}catch(e){return JSON.stringify({ok:false,error:String(e.message)})}})()"
        self._timeout.start(30000)

        def finished(result):
            if self._closed or epoch != self._epoch:
                return
            self._timeout.stop()
            try:
                response = json.loads(result)
                if not response.get("ok"):
                    raise ValueError(response.get("error", "导入失败"))
                score = ScoreIR.from_dict(response["score"])
            except (ValueError, TypeError, KeyError) as error:
                failure(str(error))
                return
            success(score)

        self._page.runJavaScript(script, finished)

    def _import_timeout(self):
        self._epoch += 1
        self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        self._failure("谱面解析超时；原项目保持不变，请使用较小或简化的导出文件")

    def cancel_import(self):
        self._epoch += 1
        self._pending = None
        self._timeout.stop()

    def show_score(self, score, part_id, kind="staff", shift=0):
        self._latest = (score, part_id, kind, shift)
        self._display_error = None
        if self.ready and not self._closed:
            args = (score.to_dict(), part_id, kind, shift)
            self._page.runJavaScript("window.showScore(" + ",".join(json.dumps(v, ensure_ascii=True) for v in args) + ")")

    def clear_score(self, message=""):
        self._latest = None
        self._display_error = message
        if self.ready and not self._closed:
            self._page.runJavaScript("window.clearScore(" + json.dumps(message, ensure_ascii=True) + ")")

    def set_position(self, sample):
        self._last_sample = sample
        if self.ready and not self._closed and self._latest is not None:
            wire = None if sample is None else {key: sample[key] for key in (
                "sourceMeasureId", "sourceOffsetQuarter", "playing", "preparing", "valid",
                "occurrenceId", "discontinuity", "loopIteration") if key in sample}
            self._page.runJavaScript("window.setScorePosition(" + json.dumps(wire, allow_nan=False) + ")")

    def set_follow(self, enabled):
        self._follow = bool(enabled)
        if self.ready and not self._closed:
            self._page.runJavaScript("window.setScoreFollow(" + json.dumps(self._follow) + ")")

    def set_active(self, active):
        self._active = bool(active)
        if self.ready and not self._closed:
            self._page.runJavaScript("window.setScoreActive(" + json.dumps(self._active) + ")")

    def shutdown(self):
        self._closed = True
        self.cancel_import()
        self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        self._page.setAudioMuted(True)
