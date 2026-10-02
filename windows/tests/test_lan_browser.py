"""Shared browser page against real localhost HTTP, SSE and clock endpoints."""

import json

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from chordcue.lan import LANServer, chart_payload
from chordcue.models import ChartDocument, ChordEvent, KeySection, MusicalKey
from chordcue.transport import StandaloneTransport


def javascript(qtbot, page, expression):
    # PySide does not consistently convert nested JS objects to Python dicts.
    results = []
    page.runJavaScript(f"JSON.stringify({expression})", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=5_000)
    assert results[0], "browser did not return a JSON result"
    return json.loads(results[0])


def wait_javascript(qtbot, page, expression):
    qtbot.waitUntil(lambda: javascript(qtbot, page, expression) is True, timeout=15_000)


def browser_state(qtbot, page):
    return javascript(qtbot, page, """(() => {
        const state = ChordCueSync.snapshot();
        return {
            chart: state.chart, sample: state.sample, connected: state.connected,
            clock: state.clockOffset,
            bars: [...document.querySelectorAll('#chart > .bar')].map(bar => ({
                symbols: [...bar.querySelectorAll('.symbol')].map(el => el.textContent),
                onsets: [...bar.querySelectorAll('.onset')].map(el => el.textContent)
            })),
            notation: document.getElementById('notation').value,
            key: document.getElementById('key').value,
            savedNotation: localStorage.getItem('chordcue-lan-notation'),
            savedKey: localStorage.getItem('chordcue-lan-key'),
            audio: document.getElementById('audioEnable').textContent,
            status: document.getElementById('status').textContent
        };
    })()""")


def select(qtbot, page, element_id, value):
    javascript(qtbot, page, """(() => {
        const input = document.getElementById(%s);
        input.value = %s;
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    })()""" % (json.dumps(element_id), json.dumps(value)))


def test_two_real_lan_browsers_render_sync_and_keep_personal_preferences(qtbot, tmp_path):
    document = ChartDocument(
        name="浏览器集成原创练习", bars=3, meter=3,
        events=(ChordEvent(1, 1, 0, "C"), ChordEvent(2, 1, 1440, "G"),
                ChordEvent(3, 2, 240, "Am")),
    )
    sections = (KeySection(1, MusicalKey(0)),)
    chart = chart_payload(document, sections, revision=17)
    transport = StandaloneTransport(document)
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    clients = []
    publisher = QTimer()
    publisher.setInterval(20)
    publisher.timeout.connect(lambda: server.publish(chart, transport.snapshot(revision=17)))
    try:
        url = server.start()[0]
        server.publish(chart, transport.snapshot(revision=17))
        publisher.start()
        for index in range(2):
            view = QWebEngineView()
            qtbot.addWidget(view)
            # An unnamed profile is off the record and has independent storage,
            # even though both clients visit the same origin and token.
            profile = QWebEngineProfile(view)
            profile.setPersistentStoragePath(str(tmp_path / f"client-{index}" / "storage"))
            profile.setCachePath(str(tmp_path / f"client-{index}" / "cache"))
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
            )
            page = QWebEnginePage(profile, view)
            page.setAudioMuted(True)
            view.setPage(page)
            clients.append((view, page, profile))
            assert profile.isOffTheRecord()
            view.resize(950, 750)
            view.show()
            with qtbot.waitSignal(page.loadFinished, timeout=20_000) as loaded:
                view.load(QUrl(url))
            assert loaded.args == [True]
            wait_javascript(qtbot, page, """!!window.ChordCueSync && (() => {
                const s = ChordCueSync.snapshot();
                return s.connected && s.chart?.revision === 17
                    && s.sample?.revision === 17 && Number.isFinite(s.clockOffset)
                    && document.querySelectorAll('#chart > .bar').length === 3;
            })()""")

        assert server.viewers == 2
        first_page, second_page = clients[0][1], clients[1][1]
        for page in (first_page, second_page):
            initial = browser_state(qtbot, page)
            assert initial["chart"] == chart
            assert initial["sample"]["revision"] == initial["chart"]["revision"] == 17
            assert initial["sample"]["meter"] == "3/4"
            assert initial["sample"]["playback"]["endBeat"] == 9
            assert initial["bars"] == [
                {"symbols": ["C", "G"], "onsets": ["第 1 拍", "第 2.50 拍"]},
                {"symbols": ["—", "Am"], "onsets": ["第 1 拍", "第 1.25 拍"]},
                {"symbols": ["%"], "onsets": []},
            ]
            assert initial["notation"] == "chords" and initial["key"] == "track"
            assert initial["savedNotation"] is None and initial["savedKey"] is None
            assert initial["audio"] == "开启声音"

        # Change both personal selectors on one browser, through DOM events used
        # by the actual page. The other browser and host remain at their defaults.
        select(qtbot, first_page, "key", "2")
        assert browser_state(qtbot, first_page)["bars"][0]["symbols"] == ["D", "A"]
        select(qtbot, first_page, "notation", "numbers")
        first = browser_state(qtbot, first_page)
        second = browser_state(qtbot, second_page)
        assert (first["key"], first["notation"], first["savedKey"], first["savedNotation"]) == (
            "2", "numbers", "2", "numbers"
        )
        assert first["bars"][0]["symbols"] == ["1", "5"]
        assert (second["key"], second["notation"], second["savedKey"], second["savedNotation"]) == (
            "track", "chords", None, None
        )
        assert second["bars"][0]["symbols"] == ["C", "G"]

        select(qtbot, second_page, "key", "5")
        second = browser_state(qtbot, second_page)
        assert second["bars"][0]["symbols"] == ["F", "C"]
        assert second["notation"] == "chords" and second["savedKey"] == "5"
        assert browser_state(qtbot, first_page)["savedKey"] == "2"
        assert transport.document == document
        assert chart_payload(transport.document, sections, revision=17) == chart
        assert first["chart"] == second["chart"] == chart
        assert not transport.snapshot()["playing"]

        publisher.stop()
        server.stop()
        for _, page, _ in clients:
            wait_javascript(qtbot, page, """!ChordCueSync.snapshot().connected
                && document.getElementById('status').textContent.includes('连接中断')""")
            assert browser_state(qtbot, page)["audio"] == "开启声音"
        assert server.viewers == 0
    finally:
        publisher.stop()
        server.stop()
        transport.close()
        # Profiles must outlive their pages; explicitly flush deferred deletion
        # before pytest removes each view and the temporary profile directories.
        for view, page, _ in clients:
            page.triggerAction(QWebEnginePage.WebAction.Stop)
            view.close()
            page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for _, _, profile in clients:
            profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
