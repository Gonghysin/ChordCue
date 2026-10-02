"""Automatic source timing and compatibility with previously saved changes."""

from fractions import Fraction
import json

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from chordcue.lan import LANServer, chart_payload
from chordcue.models import ChartDocument, ChordEvent, KeySection, MusicalKey, TimingChange, document_with_score
from chordcue.parsing import format_manual, parse_manual
from chordcue.project import load_project
from chordcue.render import layout_chart
from chordcue.theory import _make_evidence
from chordcue.timing import apply_timing, measure_quarters
from chordcue.transport import StandaloneTransport
from test_lan_browser import javascript, wait_javascript
from test_transport import FakeClock
from test_score_render_ui import FakeScore, score
from test_ui import window as isolated_window

window = isolated_window


def manual_document():
    return ChartDocument(name="变拍预设", bars=3, forced_key=MusicalKey(0), events=(
        ChordEvent(0, 1, 0, "C"), ChordEvent(1, 2, 2400, "G"), ChordEvent(2, 3, 3120, "Am")))


ROWS = (TimingChange(1, 120, 4, 4), TimingChange(2, 90, 6, 8), TimingChange(3, 150, 7, 8))


def test_saved_legacy_changes_keep_editor_save_slider_and_visible_timing(window, tmp_path):
    clock = FakeClock()
    window.transport.close()
    window.transport = StandaloneTransport(manual_document(), clock)
    window._replace_document(apply_timing(window.document, ROWS))
    assert window.document.score is None
    assert not window.chords_edit.isReadOnly()
    assert not window.bpm.isEnabled() and not window.meter.isEnabled()
    assert window.dirty
    window.seek_slider.setValue(7*960)
    window._slider_seek()
    assert window.transport.snapshot()["meter"] == "7/8"
    window._tick()
    assert "150" in window.timing_status.text() and "7/8" in window.timing_status.text()
    assert window.seek_slider.maximum() == 105*96
    assert window.chart.bars[1].duration == 3
    window._seek(2, 3.5)
    assert window.chart.phase == pytest.approx(2.5/3)
    assert window.position.text() == "002 · ♩+2.50"
    window.chords_edit.setPlainText("1 C\n2.3.3.0 G\n3.4.2.0 Am")
    assert window.apply_editor()
    assert [event.tick for event in window.document.events] == [0, 2400, 3120]
    path = tmp_path / "presets.chordcue.json"
    window.project_path = path
    assert window.save_project()
    assert json.loads(path.read_text("utf-8"))["schemaVersion"] == 3
    assert load_project(path) == window.document
    window.transport.stop()
    window.transport.play()
    clock.advance(400_000_000 + 2_000_000_000)
    window._tick()
    assert "90" in window.timing_status.text() and "6/8" in window.timing_status.text()
    clock.advance(2_000_000_000)
    window._tick()
    assert "150" in window.timing_status.text() and "7/8" in window.timing_status.text()
    window._saved_document = window.document


def test_quarter_text_roundtrip_and_per_bar_capacity():
    document = apply_timing(manual_document(), ROWS)
    text = format_manual(document.events)
    assert parse_manual(text, bar_quarters=lambda bar: measure_quarters(document, bar)) == document.events
    assert parse_manual("3.4.2.239 Am", bar_quarters=lambda bar: measure_quarters(document, bar))[0].tick == 3359
    with pytest.raises(ValueError, match="Line 1"):
        parse_manual("3.4.3.0 Am", bar_quarters=lambda bar: measure_quarters(document, bar))
    with pytest.raises(ValueError, match="Line 1"):
        parse_manual("2.4 G", bar_quarters=lambda bar: measure_quarters(document, bar))


def test_imported_source_shows_automatic_timing_and_saves_original_maps(window, tmp_path):
    window._score_factory = FakeScore
    original = document_with_score(score())
    window._replace_document(original)
    plan = window.transport.plan
    for segment in plan.segments:
        window.transport.seek_quarter(float(segment.start_quarter))
        window._tick()
        sample = window.transport.snapshot()
        assert sample["bpm"] == segment.bpm
        assert f"{segment.bpm:g}" in window.timing_status.text()
        assert sample["meter"] in window.timing_status.text()
    assert window.document.score is original.score
    assert not window.document.timing_changes
    path = tmp_path / "source-timing.json"
    window.project_path = path
    assert window.save_project()
    assert json.loads(path.read_text("utf-8"))["schemaVersion"] == 2
    assert load_project(path) == window.document


def test_variable_duration_chart_pdf_and_theory_keep_onsets(qapp, tmp_path):
    from chordcue.render import export_pdf
    document = apply_timing(manual_document(), ROWS)
    bars = layout_chart(document)
    assert [bar.meter for bar in bars] == [(4, 4), (6, 8), (7, 8)]
    assert bars[1].spans[1].start == pytest.approx(2.5/3)
    assert bars[2].spans[1].start == pytest.approx(3.25/3.5)
    assert "♩=90" in bars[1].section
    path = tmp_path / "presets.pdf"
    export_pdf(document, path)
    text = PdfReader(path).pages[0].extract_text()
    assert "6/8" in text and "7/8" in text and "90" in text and "150" in text
    evidence = _make_evidence(document.events, 4, 3, (0, 4, 7, 10.5))
    assert [(item.start, item.end) for item in evidence] == [(0, 6.5), (6.5, 10.25), (10.25, 10.5)]


def test_real_lan_late_join_seek_and_changed_chart_route(qtbot):
    document = apply_timing(manual_document(), ROWS)
    transport = StandaloneTransport(document)
    sections = (KeySection(1, MusicalKey(0)),)
    chart = chart_payload(document, sections, 110)
    assert "score" not in chart
    assert chart["measures"][2]["duration"] == {"numerator": 7, "denominator": 2}
    assert measure_quarters(document, 2) == Fraction(3)
    chart["route"] = transport.plan.to_dict()
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    view = QWebEngineView()
    qtbot.addWidget(view)
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    page.setAudioMuted(True)
    view.setPage(page)
    publisher = QTimer()
    publisher.setInterval(20)
    publisher.timeout.connect(lambda: server.publish(chart, transport.snapshot(chart["revision"], include_route=False)))
    try:
        transport.seek(2, 3.5)
        url = server.start()[0]
        server.publish(chart, transport.snapshot(110, include_route=False))
        publisher.start()
        view.resize(700, 520)
        view.show()
        with qtbot.waitSignal(page.loadFinished, timeout=20000):
            view.load(QUrl(url))
        wait_javascript(qtbot, page, "window.ChordCueSync?.snapshot().device?.applied===true")
        wait_javascript(qtbot, page, "document.getElementById('metadata').textContent.includes('6/8')")
        state = javascript(qtbot, page, """({route:ChordCueSync.snapshot().chart.route,
            onsets:[...document.querySelectorAll('#chart>.bar')][1].querySelector('.onset')?.textContent})""")
        assert state["route"] == chart["route"]
        assert state["onsets"] is not None
        transport.seek(3, 1)
        wait_javascript(qtbot, page, "document.getElementById('metadata').textContent.includes('7/8') && document.getElementById('metadata').textContent.includes('150')")
        replacement = apply_timing(document, (ROWS[0], TimingChange(2, 80, 7, 8)))
        transport.set_document(replacement)
        chart = chart_payload(replacement, sections, 111)
        chart["route"] = transport.plan.to_dict()
        transport.seek(2, 1)
        wait_javascript(qtbot, page, "ChordCueSync.snapshot().chart.revision===111 && ChordCueSync.snapshot().device.applied===true")
        wait_javascript(qtbot, page, "document.getElementById('metadata').textContent.includes('80') && document.getElementById('metadata').textContent.includes('7/8')")
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
