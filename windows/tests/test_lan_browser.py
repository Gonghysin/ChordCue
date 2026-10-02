"""Shared browser page against real localhost HTTP, SSE and clock endpoints."""

import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from chordcue.lan import LANServer, chart_payload
from chordcue.models import ChartDocument, ChordEvent, KeySection, MusicalKey, document_with_score
from chordcue.score_models import ScoreIR
from chordcue.score_ui import DevicePanel
from chordcue.transport import StandaloneTransport
from test_score_panel import multi_row_score
from score_fixtures import large_score


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


def test_audio_output_estimate_is_reported_without_inventing_zero(qtbot, tmp_path):
    """Real browser/AudioContext and HTTP telemetry; latency properties are controlled."""
    document = ChartDocument()
    chart = chart_payload(document, (), 7)
    transport = StandaloneTransport(document)
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    view = QWebEngineView()
    qtbot.addWidget(view)
    device_panel = DevicePanel(lambda *args: None, lambda error: None)
    qtbot.addWidget(device_panel)
    profile = QWebEngineProfile(view)
    profile.setCachePath(str(tmp_path / "audio-cache"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    page = QWebEnginePage(profile, view)
    page.setAudioMuted(True)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    view.setPage(page)
    publisher = QTimer()
    publisher.setInterval(20)
    publisher.timeout.connect(lambda: server.publish_prepared(chart, transport.snapshot(7, include_route=False)))
    try:
        url = server.start()[0]
        server.publish_prepared(chart, transport.snapshot(7, include_route=False))
        publisher.start()
        view.resize(950, 750)
        view.show()
        with qtbot.waitSignal(page.loadFinished, timeout=20_000) as loaded:
            view.load(QUrl(url))
        assert loaded.args == [True]
        wait_javascript(qtbot, page, "!!window.ChordCueSync?.snapshot().device?.applied")
        qtbot.waitUntil(lambda: len(server.devices) == 1 and server.devices[0]["clockStatus"] == "valid",
                        timeout=8_000)
        assert server.devices[0]["audioOutputDelayMs"] is None
        device_panel.update_devices(server.devices, ())
        assert device_panel.item(0, 6).text() == "未提供估计"
        assert javascript(qtbot, page, "ChordCueMetronome.diagnostics().audioOutputDelayMs") is None

        # Only the reported latency attributes are overridden. Resume/suspend,
        # WebAudio state, page metrics, POST and registry remain real.
        javascript(qtbot, page, """(() => {
            const NativeAudio = window.AudioContext;
            window.testLatency = {base: 0, output: 0};
            window.AudioContext = class extends NativeAudio {
                constructor(options) { super(options); window.testAudioContext = this; }
                get baseLatency() { return window.testLatency.base; }
                get outputLatency() { return window.testLatency.output; }
            };
            document.getElementById('audioEnable').click();
            return true;
        })()""")
        wait_javascript(qtbot, page, "window.testAudioContext?.state === 'running'")
        assert javascript(qtbot, page, "ChordCueMetronome.diagnostics().audioOutputDelayMs") is None
        qtbot.waitUntil(lambda: server.devices[0]["audioOutputDelayMs"] is None, timeout=5_000)

        javascript(qtbot, page, "(() => {testLatency.base = .008; testLatency.output = .024; return true;})()")
        wait_javascript(qtbot, page, "Math.abs(ChordCueMetronome.diagnostics().audioOutputDelayMs - 32) < 1e-6")
        qtbot.waitUntil(lambda: server.devices[0]["audioOutputDelayMs"] is not None
                        and abs(server.devices[0]["audioOutputDelayMs"] - 32) < 1e-6, timeout=8_000)
        assert server.devices[0]["clockStatus"] == "valid"
        assert server.devices[0]["rttMs"] is not None
        device_panel.update_devices(server.devices, ())
        assert device_panel.item(0, 6).text() == "32.0 ms"

        javascript(qtbot, page, "(() => {testAudioContext.suspend(); return true;})()")
        wait_javascript(qtbot, page, "testAudioContext.state === 'suspended'")
        assert javascript(qtbot, page, "ChordCueMetronome.diagnostics().audioOutputDelayMs") is None
        qtbot.waitUntil(lambda: server.devices[0]["audioOutputDelayMs"] is None, timeout=8_000)
        device_panel.update_devices(server.devices, ())
        assert device_panel.item(0, 6).text() == "未提供估计"
    finally:
        publisher.stop()
        page.triggerAction(QWebEnginePage.WebAction.Stop)
        server.stop()
        transport.close()
        view.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
        qtbot.waitUntil(lambda: len(server.devices) == 2 and all(
            item["clockStatus"] == "valid" and item["rttMs"] is not None
            and item["clockProbeAgeMs"] < 10000 for item in server.devices), timeout=8_000)
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


def test_lan_score_rows_align_below_header_and_stale_position_clears_boxes(qtbot):
    document = document_with_score(multi_row_score())
    chart = chart_payload(document, (), revision=71)
    transport = StandaloneTransport(document)
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    view = QWebEngineView()
    qtbot.addWidget(view)
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    page.setAudioMuted(True)
    view.setPage(page)
    publisher = QTimer()
    publisher.setInterval(20)
    publisher.timeout.connect(lambda: server.publish(chart, transport.snapshot(revision=71)))
    try:
        url = server.start()[0]
        server.publish(chart, transport.snapshot(revision=71))
        publisher.start()
        view.resize(680, 520)
        view.show()
        with qtbot.waitSignal(page.loadFinished, timeout=20000):
            view.load(QUrl(url))
        wait_javascript(qtbot, page, "window.ChordCueSync?.snapshot().device?.applied===true")
        client_id = javascript(qtbot, page, "ChordCueSync.snapshot().device.clientId")
        for kind in ("staff", "tab"):
            server.assign_device(client_id, "p1", kind)
            wait_javascript(qtbot, page, "scoreView?.rendered===true && scoreView?.view==='"+kind+"'")
            transport.seek(24, 1)
            transport.play()
            wait_javascript(qtbot, page, "scoreView?.highlights.some(x=>!x.hidden)===true && !ChordCueSync.snapshot().sample.preparing")
            wait_javascript(qtbot, page, """(() => {
                const entry=ChordCueScoreView.activeEntries(scoreView.converted.beatMap.get(scoreView.latestSample.sourceMeasureId),scoreView.latestSample.sourceOffsetQuarter)[0];
                const bounds=scoreView.api.renderer.boundsLookup.findBeat(entry.beat);
                const system=bounds.barBounds.masterBarBounds.staffSystemBounds;
                const top=scoreView.element.getBoundingClientRect().top+scoreView._origin().y+system.realBounds.y;
                return Math.abs(top-document.querySelector('header').getBoundingClientRect().height)<=8;
            })()""")
            transport.pause()
        publisher.stop()
        wait_javascript(qtbot, page, "scoreView.highlights.every(x=>x.hidden)")
    finally:
        publisher.stop()
        server.stop()
        transport.close()
        page.triggerAction(QWebEnginePage.WebAction.Stop)
        view.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_large_score_crosses_old_budget_and_renders_assigned_views(qtbot):
    document = document_with_score(large_score())
    playback = StandaloneTransport(document)
    chart = chart_payload(document, (), revision=83)
    chart["route"] = playback.plan.to_dict()
    assert len(json.dumps(chart).encode()) > 2 * 1024 * 1024
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    view = QWebEngineView()
    qtbot.addWidget(view)
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    page.setAudioMuted(True)
    view.setPage(page)
    publisher = QTimer()
    publisher.setInterval(20)
    publisher.timeout.connect(lambda: server.publish_prepared(chart, playback.snapshot(83, include_route=False)))
    try:
        url = server.start()[0]
        server.publish_prepared(chart, playback.snapshot(83, include_route=False))
        publisher.start()
        view.resize(900, 700)
        view.show()
        with qtbot.waitSignal(page.loadFinished, timeout=20000):
            view.load(QUrl(url))
        wait_javascript(qtbot, page, "ChordCueSync.snapshot().device?.applied===true")
        assert javascript(qtbot, page, "ChordCueSync.snapshot().chart.score.warnings.length") >= 1200
        client_id = javascript(qtbot, page, "ChordCueSync.snapshot().device.clientId")
        for kind in ("staff", "tab"):
            server.assign_device(client_id, "p1", kind)
            wait_javascript(qtbot, page, "scoreView?.rendered===true && scoreView.view==='" + kind + "' && ChordCueSync.snapshot().device.applied===true && document.querySelectorAll('#score svg').length>0")
            assert server.devices[0]["applied"]
        playback.play()
        wait_javascript(qtbot, page, "ChordCueSync.snapshot().sample.playing===true && scoreView.highlights.some(x=>!x.hidden)")
    finally:
        publisher.stop()
        server.stop()
        playback.close()
        page.triggerAction(QWebEnginePage.WebAction.Stop)
        view.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_three_registered_score_browsers_apply_independent_views_ack_and_reconnect(qtbot, tmp_path):
    score = ScoreIR.from_dict(json.loads(
        (Path(__file__).parent / "fixtures" / "issue1-original.score.json").read_text(encoding="utf-8")
    ))
    document = document_with_score(score)
    chart = chart_payload(document, (), revision=29)
    transport = StandaloneTransport(document)
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    clients = []
    publisher = QTimer()
    publisher.setInterval(20)
    publisher.timeout.connect(lambda: server.publish(chart, transport.snapshot(revision=29)))
    try:
        url = server.start()[0]
        server.publish(chart, transport.snapshot(revision=29))
        publisher.start()
        for index in range(3):
            view = QWebEngineView()
            qtbot.addWidget(view)
            profile = QWebEngineProfile(view)
            profile.setPersistentStoragePath(str(tmp_path / f"score-client-{index}" / "storage"))
            profile.setCachePath(str(tmp_path / f"score-client-{index}" / "cache"))
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            page = QWebEnginePage(profile, view)
            page.setAudioMuted(True)
            view.setPage(page)
            clients.append((view, page, profile))
            view.resize(1000, 800)
            view.show()
            with qtbot.waitSignal(page.loadFinished, timeout=20_000) as loaded:
                view.load(QUrl(url))
            assert loaded.args == [True]
            wait_javascript(qtbot, page, """!!window.ChordCueSync && (() => {
                const s = ChordCueSync.snapshot();
                return s.connected && s.device?.applied && s.chart?.revision === 29
                    && s.sample?.revision === 29 && Number.isFinite(s.clockOffset);
            })()""")

        pages = [item[1] for item in clients]
        client_ids = [javascript(qtbot, page, "ChordCueSync.snapshot().device.clientId") for page in pages]
        assert len(set(client_ids)) == 3
        qtbot.waitUntil(lambda: len(server.devices) == 3, timeout=5_000)
        expected = (("p1", "tab"), ("p2", "staff"), ("p3", "metronome"))
        for client_id, (part_id, kind) in zip(client_ids, expected):
            server.assign_device(client_id, part_id, kind)
        for page, (part_id, kind) in zip(pages, expected):
            wait_javascript(qtbot, page, """(() => {
                const s = ChordCueSync.snapshot();
                return s.connected && s.device?.applied
                    && s.assignment?.partId === %s && s.assignment?.view === %s && s.page === %s;
            })()""" % (json.dumps(part_id), json.dumps(kind), json.dumps(kind)))
            assert javascript(qtbot, page, "document.getElementById('audioEnable').textContent") == "开启声音"
        for page in pages[:2]:
            wait_javascript(qtbot, page, """scoreView?.rendered === true
                && document.querySelectorAll('#score svg').length > 0""")
        assert javascript(qtbot, pages[2], "!document.getElementById('metronome').hidden")
        qtbot.waitUntil(lambda: all(item["applied"] for item in server.devices), timeout=8_000)

        unaffected = [javascript(qtbot, page, "ChordCueSync.snapshot().assignment") for page in pages[1:]]
        revised = server.assign_device(client_ids[0], "p1", "numbers", "原创练习吉他平板")
        wait_javascript(qtbot, pages[0], """(() => {
            const s = ChordCueSync.snapshot();
            return s.device?.applied && s.assignment?.view === 'numbers'
                && s.assignment?.assignmentRevision === %s && s.page === 'numbers';
        })()""" % revised["assignmentRevision"])
        assert [javascript(qtbot, page, "ChordCueSync.snapshot().assignment") for page in pages[1:]] == unaffected
        assert javascript(qtbot, pages[0], """[...document.querySelectorAll('#chart .symbol')]
            .map(value => value.textContent).includes('1maj₇/3')""")
        assert javascript(qtbot, pages[0], "ChordCueSync.snapshot().chart") == chart
        qtbot.waitUntil(lambda: next(item for item in server.devices if item["clientId"] == client_ids[0])["applied"], timeout=8_000)

        # Close this actual server-side SSE socket. Only its authenticated tab
        # reconnects, registering with the saved recovery credential.
        def disconnect_first():
            for peer in tuple(server._peers):
                if peer.streaming and peer.client_id == client_ids[0]:
                    peer.close()

        assert server._loop is not None
        server._loop.call_soon_threadsafe(disconnect_first)
        wait_javascript(qtbot, pages[0], "!ChordCueSync.snapshot().connected && ChordCueSync.snapshot().sample === null")
        wait_javascript(qtbot, pages[0], """(() => {
            const s = ChordCueSync.snapshot();
            return s.connected && s.device?.applied && s.device?.clientId === %s
                && s.assignment?.assignmentRevision === %s && s.assignment?.view === 'numbers';
        })()""" % (json.dumps(client_ids[0]), revised["assignmentRevision"]))
        assert [javascript(qtbot, page, "ChordCueSync.snapshot().assignment") for page in pages[1:]] == unaffected

        with qtbot.waitSignal(pages[0].loadFinished, timeout=20_000):
            pages[0].triggerAction(QWebEnginePage.WebAction.Reload)
        wait_javascript(qtbot, pages[0], """(() => {
            const s = window.ChordCueSync?.snapshot();
            return !!s && s.connected && s.device?.applied && s.device?.clientId === %s
                && s.assignment?.label === '原创练习吉他平板' && s.assignment?.view === 'numbers';
        })()""" % json.dumps(client_ids[0]))
        assert len(server.devices) == 3
        assert not transport.snapshot()["playing"]
    finally:
        publisher.stop()
        server.stop()
        transport.close()
        for view, page, _ in clients:
            page.triggerAction(QWebEnginePage.WebAction.Stop)
            view.close()
            page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for _, _, profile in clients:
            profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
