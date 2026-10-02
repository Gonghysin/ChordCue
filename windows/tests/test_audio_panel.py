"""Real Qt page/bridge tests; no speaker output is enabled by these tests."""

import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage
import pytest

from chordcue.audio import AudioPanel, _LocalResources
from chordcue.models import ChartDocument
from chordcue.transport import StandaloneTransport


@pytest.fixture
def panel(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "chordcue.audio.QStandardPaths.writableLocation", lambda _kind: str(tmp_path)
    )
    widget = AudioPanel()
    qtbot.addWidget(widget)
    widget.page().setAudioMuted(True)
    widget.resize(900, 850)
    widget.show()
    qtbot.waitUntil(lambda: widget.ready, timeout=20_000)
    yield widget
    widget.shutdown()


def javascript(qtbot, panel, source):
    result = []
    panel.page().runJavaScript("JSON.stringify(" + source + ")", result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=5_000)
    return json.loads(result[0])


def test_page_loads_local_resources_and_clock_bridge(panel, qtbot, tmp_path):
    # Page/bridge readiness can precede the first reliable clock response on a
    # cold runner. Await real calibration rather than trusting a congested probe.
    qtbot.waitUntil(lambda: javascript(qtbot, panel, "(() => {"
                    "const s=ChordCueSync.snapshot();"
                    "return s.clockDiagnostics.status==='valid' && Number.isFinite(s.clockOffset);"
                    "})()"), timeout=5_000)
    snapshot = javascript(qtbot, panel, "({native:ChordCueNative,bridge:ChordCueBridgeReady,"
                          "clock:ChordCueSync.snapshot().clockOffset,"
                          "enabled:document.getElementById('audioEnable').textContent})")
    assert snapshot["native"] is True
    assert snapshot["bridge"] is True
    assert snapshot["clock"] is not None
    assert snapshot["enabled"] == "开启声音"
    assert Path(panel._profile.persistentStoragePath()).is_relative_to(tmp_path)
    assert not panel._profile.isOffTheRecord()
    assert panel.page().url().isLocalFile()


def test_transport_revision_and_native_state_reach_real_page(panel, qtbot):
    transport = StandaloneTransport(ChartDocument())
    panel.update_transport(transport.snapshot(revision=37), "桥接验证")
    qtbot.waitUntil(lambda: not panel._sending, timeout=5_000)
    snapshot = javascript(qtbot, panel, "ChordCueSync.snapshot()")
    assert snapshot["chart"]["revision"] == 37
    assert snapshot["sample"]["revision"] == 37
    assert snapshot["chart"]["name"] == "桥接验证"
    assert snapshot["sample"]["playback"]["endBeat"] == 64
    assert snapshot["connected"] is True
    assert "Logic" not in javascript(
        qtbot, panel, "document.getElementById('patternDescription').textContent"
    )


def test_bridge_reports_audio_state_and_hide_keeps_page_active(panel, qtbot):
    changes = []
    panel.enabled_changed.connect(changes.append)
    # Exercise only the restricted notification slot; never start speaker audio.
    panel.page().runJavaScript("ChordCueBridge.audioState(true)")
    qtbot.waitUntil(lambda: changes == [True])
    page = panel.page()
    panel.hide()
    qtbot.wait(100)
    assert panel.page() is page
    assert page.lifecycleState() == QWebEnginePage.LifecycleState.Active
    assert panel.ready
    panel.shutdown()
    panel.shutdown()
    assert changes == [True, False]
    assert not panel.ready


def test_remote_navigation_and_unrelated_local_files_are_blocked(panel, tmp_path):
    page = panel.page()
    kind = QWebEnginePage.NavigationType.NavigationTypeOther
    assert not page.acceptNavigationRequest(QUrl("https://example.invalid/"), kind, True)
    assert not page.acceptNavigationRequest(QUrl.fromLocalFile(str(tmp_path / "other.html")), kind, True)
    assert not page.acceptNavigationRequest(page.url(), kind, False)
    assert page.acceptNavigationRequest(page.url(), kind, True)
    assert page.createWindow(QWebEnginePage.WebWindowType.WebBrowserTab) is None


def test_request_allowlist_is_exact_and_denies_remote_and_qrc(panel, tmp_path):
    allowed = tmp_path / "Broadcast.html"
    interceptor = _LocalResources({allowed.resolve()}, panel)

    class Request:
        def __init__(self, url):
            self.url, self.blocked = QUrl(url), None

        def requestUrl(self):
            return self.url

        def block(self, value):
            self.blocked = value

    for url, denied in (
        (QUrl.fromLocalFile(str(allowed)).toString(), False),
        (QUrl.fromLocalFile(str(tmp_path / "private.txt")).toString(), True),
        ("https://example.invalid/", True),
        ("http://127.0.0.1:9999/", True),
        ("qrc:///private.txt", True),
        ("data:text/plain,secret", True),
    ):
        request = Request(url)
        interceptor.interceptRequest(request)
        assert request.blocked is denied


def test_shutdown_cannot_reenable_or_accept_new_transport(panel, qtbot):
    panel.shutdown()
    panel.set_enabled(True)
    panel.update_transport(StandaloneTransport(ChartDocument()).snapshot())
    assert not panel.ready
    assert panel._pending is None
    assert panel._pending_enabled is None


def test_pattern_preferences_reload_but_audio_stays_disarmed(panel, qtbot):
    javascript(qtbot, panel, "(localStorage.setItem('chordcue-metro-mode','drums'), true)")
    with qtbot.waitSignal(panel.page().loadFinished, timeout=20_000):
        panel.page().triggerAction(QWebEnginePage.WebAction.Reload)
    qtbot.waitUntil(lambda: panel.ready, timeout=20_000)
    state = javascript(qtbot, panel, "({mode:document.getElementById('metroMode').value,"
                       "enabled:document.getElementById('audioEnable').textContent})")
    assert state == {"mode": "drums", "enabled": "开启声音"}
    assert not panel._enabled
