from dataclasses import replace
import json
from pathlib import Path

from pypdf import PdfReader
from PySide6.QtWidgets import QDialog, QWidget

from chordcue.models import ChartDocument, MusicalKey, document_with_score
from chordcue.render import display_shift, export_pdf, layout_chart, onset_label
from chordcue.score_models import ScoreIR
from chordcue.score_ui import DevicePanel
from test_ui import FakeAudio, FakeLAN, FakeSession
from chordcue.settings import Settings
from chordcue.ui import MainWindow


def score():
    return ScoreIR.from_dict(json.loads((Path(__file__).parent / "fixtures/issue1-original.score.json").read_text("utf-8")))


class FakeScore(QWidget):
    def show_score(self, *args):
        self.shown = args

    def set_position(self, sample):
        self.sample = sample

    def clear_score(self, message=""):
        self.shown = None
        self.error = message

    def import_file(self, path, success, failure):
        success(score())

    def shutdown(self):
        self.closed = True


def test_source_chords_use_exact_offsets_meter_key_and_no_inference(qapp, tmp_path):
    document = document_with_score(score(), "p1")
    bars = layout_chart(document)
    assert bars[2].meter == (6, 8)
    assert bars[2].duration == 3
    assert bars[0].spans[-1].start == .375
    assert onset_label(bars[0].spans[-1], 4) == "♩ +3/2"
    assert bars[0].spans[0].text == "Cmaj7/E"
    empty = layout_chart(replace(document, selected_part_id="p3"))
    assert all(bar.spans[0].text == "无和弦标记" for bar in empty)
    path = tmp_path / "source.pdf"
    export_pdf(document, path)
    text = PdfReader(path).pages[0].extract_text()
    assert "6/8" in text
    assert "Cmaj7/E" in text
    assert "精确和弦起点" in text
    assert "四分音符" in text


def test_import_preview_selection_save_and_cancel_preserves_project(qtbot, tmp_path, monkeypatch):
    window = MainWindow(settings=Settings(tmp_path / "ui.ini"), audio_factory=FakeAudio,
        lan_factory=FakeLAN, session_factory=FakeSession, score_factory=FakeScore,
        chart_factory=lambda doc, sections, revision: {"revision": revision})
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "_saved_document", w.document))
    previous = window.document
    monkeypatch.setattr("chordcue.ui.ScorePreview.exec", lambda self: QDialog.DialogCode.Rejected)
    window.import_score_path("original.musicxml")
    assert window.document is previous
    monkeypatch.setattr("chordcue.ui.ScorePreview.exec", lambda self: QDialog.DialogCode.Accepted)
    window.import_score_path("original.musicxml")
    assert window.document.score == score()
    assert window.score_panel.shown[1] == "p1"
    assert window.tabs.currentWidget() is window.score_page
    assert window.score_page.isAncestorOf(window.score_kind)
    assert window.score_page.isAncestorOf(window.score_part)
    assert not window.chart_page.isAncestorOf(window.score_kind)
    assert not window.bpm.isEnabled() and not window.bars.isEnabled() and not window.meter.isEnabled()
    window.score_part.setCurrentIndex(1)
    assert window.document.selected_part_id == "p2"
    path = tmp_path / "selected.json"
    window.project_path = path
    assert window.save_project()
    assert json.loads(path.read_text("utf-8"))["selectedPartId"] == "p2"
    window.close()


def test_score_toolbar_disables_tab_for_a_part_without_fingering(qtbot, tmp_path):
    window = MainWindow(settings=Settings(tmp_path / "score-toolbar.ini"), audio_factory=FakeAudio,
        lan_factory=FakeLAN, session_factory=FakeSession, score_factory=FakeScore,
        chart_factory=lambda doc, sections, revision: {"revision": revision})
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "_saved_document", w.document))
    window._replace_document(document_with_score(score(), "p1"))
    window.score_kind.setCurrentIndex(1)
    assert window.score_kind.currentData() == "tab"
    window._set_combo(window.score_part, "p3")
    assert not window.score_kind.model().item(1).isEnabled()
    assert window.score_kind.currentData() == "staff"
    window._saved_document = window.document
    window.close()


def test_device_panel_changes_only_selected_client(qtbot):
    calls = []
    panel = DevicePanel(lambda *args: calls.append(args), lambda error: None)
    qtbot.addWidget(panel)
    devices = [{"clientId": f"device{i}", "label": f"音乐人{i}", "partId": "p1", "view": "staff",
        "connected": True, "syncStatus": "synchronized", "applied": True, "rttMs": 2+i,
        "clockOffsetMs": -4+i, "audioOutputDelayMs": None, "lastSeenSeconds": 1, "browser": "Test"}
        for i in range(3)]
    panel.update_devices(devices, score().parts)
    assert not calls
    panel.cellWidget(1, 2).setCurrentIndex(1)
    assert calls[-1][0:2] == ("device1", "p2")
    assert all(call[0] == "device1" for call in calls)
    assert panel.cellWidget(0, 2).currentData() == "p1"
    assert panel.cellWidget(2, 2).currentData() == "p1"
    assert panel.item(0, 4).text() == "2.0 ms"
    assert panel.item(0, 5).text() == "未提供 / 未测量 / 未测量"
    assert "-4.0 ms" in panel.item(0, 5).toolTip()
    assert panel.horizontalHeaderItem(6).text() == "音频输出延迟估计"
    assert panel.item(0, 6).text() == "未提供估计"
    assert "不包含手动额外补偿" in panel.item(0, 6).toolTip()
    devices[0]["audioOutputDelayMs"] = 32.5
    panel.update_devices(devices, score().parts)
    assert panel.item(0, 6).text() == "32.5 ms"
    devices[0]["audioOutputDelayMs"] = 0.0
    panel.update_devices(devices, score().parts)
    assert panel.item(0, 6).text() == "0.0 ms"


def test_source_editor_disables_ignored_operations_and_keeps_display_keys(qtbot, tmp_path, monkeypatch):
    window = MainWindow(settings=Settings(tmp_path / "source-editor.ini"), audio_factory=FakeAudio,
        lan_factory=FakeLAN, session_factory=FakeSession, score_factory=FakeScore,
        chart_factory=lambda doc, sections, revision: {"revision": revision})
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "_saved_document", w.document))
    source = document_with_score(score())
    window._replace_document(source)
    assert window.chords_edit.isReadOnly()
    assert not window.sections_edit.isEnabled()
    assert not window.detect_changes.isEnabled()
    assert not window.import_text_button.isEnabled()
    monkeypatch.setattr("chordcue.ui.QFileDialog.getOpenFileName",
        lambda *args: (_ for _ in ()).throw(AssertionError("source mode must not open a text import")))
    window.import_text()
    assert window.document == source
    window._set_combo(window.forced_key, MusicalKey(2))
    assert window.apply_editor()
    assert window.document.score is source.score
    assert window.document.forced_key == MusicalKey(2)
    assert window.document.manual_sections == source.manual_sections
    window._replace_document(ChartDocument())
    assert not window.chords_edit.isReadOnly()
    assert window.sections_edit.isEnabled()
    assert window.detect_changes.isEnabled()
    assert window.import_text_button.isEnabled()


def test_staff_and_chord_transposition_share_initial_exact_source_key(qtbot, tmp_path):
    raw = score().to_dict()
    raw["keyChanges"].append({"measureId": raw["measures"][0]["id"],
        "offset": {"numerator": 2, "denominator": 1}, "fifths": 2, "mode": "major"})
    source = document_with_score(ScoreIR.from_dict(raw))
    window = MainWindow(settings=Settings(tmp_path / "key-shift.ini"), audio_factory=FakeAudio,
        lan_factory=FakeLAN, session_factory=FakeSession, score_factory=FakeScore,
        chart_factory=lambda doc, sections, revision: {"revision": revision})
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "_saved_document", w.document))
    window._replace_document(source)
    window._set_combo(window.display_key, "c")
    assert display_shift(source, window._sections, "c") == 0
    assert window.score_panel.shown[-1] == 0
    assert layout_chart(source, mode="c")[0].spans[0].text == "Cmaj7/E"
    window._set_combo(window.display_key, "key:2")
    assert window.score_panel.shown[-1] == 2
    assert layout_chart(source, mode="key:2")[0].spans[0].text == "Dmaj7/F♯"


def test_unknown_key_failure_clears_previous_part_and_recovers(qtbot, tmp_path):
    raw = score().to_dict()
    raw["keyChanges"] = []
    source = document_with_score(ScoreIR.from_dict(raw))
    window = MainWindow(settings=Settings(tmp_path / "unknown-key.ini"), audio_factory=FakeAudio,
        lan_factory=FakeLAN, session_factory=FakeSession, score_factory=FakeScore,
        chart_factory=lambda doc, sections, revision: {"revision": revision})
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "_saved_document", w.document))
    window._replace_document(source)
    assert window.score_panel.shown[1] == "p1"
    window._set_combo(window.display_key, "c")
    window.score_part.setCurrentIndex(1)
    assert window.document.selected_part_id == "p2"
    assert window.score_panel.shown is None
    assert "调性" in window.score_panel.error
    assert window.chart.bars == ()
    window._set_combo(window.display_key, "track")
    assert window.score_panel.shown[1] == "p2"
    assert window.chart.bars
    window._replace_document(ChartDocument())
    assert not window.tabs.isTabVisible(window.tabs.indexOf(window.score_panel))
