import pytest
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from chordcue.models import ChartDocument, ChordEvent
from chordcue.project import save_project
from chordcue.settings import Settings
from chordcue.ui import MainWindow, parse_sections


class FakeAudio(QWidget):
    enabled_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self.closed = False
        self.updates = []

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.enabled_changed.emit(enabled)

    def update_transport(self, payload, name=""):
        self.updates.append(payload)

    def shutdown(self):
        self.closed = True


class FakeLAN:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.updates = []

    def start(self):
        self.started += 1
        return ["http://192.168.1.2:8000/join/secret"]

    def stop(self):
        self.stopped += 1

    def publish(self, chart, transport):
        self.updates.append((chart, transport))


class FakeSession:
    def __init__(self, window, callback):
        self.callback = callback
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def window(qtbot, tmp_path):
    win = MainWindow(settings=Settings(tmp_path / "settings.ini"), audio_factory=FakeAudio,
                     lan_factory=FakeLAN, session_factory=FakeSession,
                     chart_factory=lambda document, sections, revision: {"revision": revision})
    def discard_for_teardown(widget):
        widget._saved_document = widget.document
        widget._editor_dirty = False
    qtbot.addWidget(win, before_close_func=discard_for_teardown)
    yield win
    win._saved_document = win.document
    win._editor_dirty = False
    win.close()


def test_defaults_off_lan_explicit_and_suspend_disarms(window):
    assert not window.transport.snapshot()["playing"]
    assert not window.audio.enabled
    assert window._lan.started == 0
    window.tabs.setCurrentIndex(1)
    window.tabs.setCurrentIndex(0)
    assert not window.audio.closed
    window.lan_action.setChecked(True)
    assert window._lan.started == 1
    assert window._lan.updates[-1][0]["revision"] == window._lan.updates[-1][1]["revision"]
    window.audio_check.setChecked(True)
    window.transport.play()
    window.session_guard.callback()
    assert not window.transport.snapshot()["playing"]
    assert not window.audio.enabled


def test_malformed_editor_does_not_replace_document(window):
    previous = window.document
    window.chords_edit.setPlainText("1 C\ninvalid")
    assert not window.apply_editor()
    assert window.document == previous
    assert "Line 2" in window.input_error.text()
    window.chords_edit.setPlainText("1.1.2.120 C/G\n2 Am")
    assert window.apply_editor()
    assert window.document.events[0].tick == 360
    assert window.dirty


def test_failed_open_preserves_live_document_and_edits(window, tmp_path):
    previous = window.document
    window.chords_edit.setPlainText("1 C")
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert not window.open_project_path(bad)
    assert window.document == previous
    assert window.chords_edit.toPlainText() == "1 C"


def test_cancel_prevents_new_open_and_close(window, tmp_path, monkeypatch):
    window.chords_edit.setPlainText("1 C")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    path = tmp_path / "valid.chordcue"
    save_project(ChartDocument(name="另一首"), path)
    original = window.document
    assert not window.new_project()
    assert not window.open_project_path(path)
    assert not window.close()
    assert window.document == original
    assert not window.audio.closed


def test_open_stops_and_disarms_and_save_round_trip(window, tmp_path):
    path = tmp_path / "valid.chordcue"
    save_project(ChartDocument(name="另一首", bars=8, meter=3, events=(ChordEvent(0, 2, 120, "G/B"),)), path)
    window.audio.set_enabled(True)
    window.transport.play()
    assert window.open_project_path(path)
    assert not window.transport.snapshot()["playing"]
    assert not window.audio.enabled
    assert not window.dirty
    window.chords_edit.setPlainText("1 C\n2.2 G")
    assert window.save_project()
    assert not window.dirty
    window.export_chart_pdf(tmp_path / "output.pdf")
    assert (tmp_path / "output.pdf").stat().st_size > 1000


def test_tick_does_not_repeat_analysis(window, monkeypatch):
    import chordcue.ui as ui
    monkeypatch.setattr(ui, "analyze_sections", lambda *_: pytest.fail("analysis at transport rate"))
    for _ in range(3):
        window._tick()


def test_loop_uses_inclusive_ui_end(window):
    window.loop_start.setValue(2)
    window.loop_end.setValue(4)
    window.loop_check.setChecked(True)
    assert window.document.loop.start_bar == 2
    assert window.document.loop.end_bar_exclusive == 5


def test_close_releases_all_resources(window):
    window.close()
    assert not window.timer.isActive()
    assert window.audio.closed
    assert window.session_guard.closed
    assert window._lan.stopped


def test_view_preferences_restore_but_outputs_remain_off(window):
    window.notation.setCurrentIndex(1)
    window._set_combo(window.display_key, "key:3")
    window.top_action.setChecked(True)
    window.audio.set_enabled(True)
    window.lan_action.setChecked(True)
    window.close()
    restored = MainWindow(settings=window.settings, audio_factory=FakeAudio,
                          lan_factory=FakeLAN, session_factory=FakeSession,
                          chart_factory=lambda d, s, r: {"revision": r})
    try:
        assert restored.notation.currentData() == "numbers"
        assert restored.display_key.currentData() == "key:3"
        assert restored.top_action.isChecked()
        assert not restored.audio.enabled
        assert restored._lan.started == 0
    finally:
        restored.close()


def test_save_dialog_uses_project_json_extension(window, tmp_path, monkeypatch):
    def choose(*args):
        assert args[2].endswith(".chordcue.json")
        assert "*.chordcue.json" in args[3]
        return str(tmp_path / "song"), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    assert window.save_project()
    assert window.project_path == tmp_path / "song.chordcue.json"
    assert window.project_path.exists()


@pytest.mark.parametrize("text", ["1 C\n1 D", "0 C", "17 C", "1 nope", "x Am"])
def test_manual_sections_reject_invalid_lines(text):
    with pytest.raises(ValueError, match="行"):
        parse_sections(text, 16)
