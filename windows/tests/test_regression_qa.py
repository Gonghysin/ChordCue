"""Independent cross-module regressions identified during A7 review."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess

from PySide6.QtCore import QObject, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QMessageBox
import pytest

from chordcue.audio import _Bridge
from chordcue.models import ChartDocument, ChordEvent, MusicalKey
from chordcue.project import load_project, save_project
from chordcue.render import export_pdf
from chordcue.transport import StandaloneTransport
from test_audio_panel import javascript, panel as isolated_panel
from test_ui import window as isolated_window

# Keep pytest's isolated widget fixture without shadowing an unused import.
window = isolated_window
panel = isolated_panel


def test_reopen_same_file_after_save_uses_the_just_saved_document(window, tmp_path, monkeypatch):
    path = tmp_path / "same.chordcue.json"
    save_project(window.document, path)
    assert window.open_project_path(path)
    changed = replace(window.document, name="Saved latest edit")
    window._replace_document(changed)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)

    assert window.open_project_path(path)
    assert load_project(path) == changed
    assert window.document == changed
    assert not window.dirty


def test_delayed_audio_enabled_notification_does_not_erase_pending_disable():
    class Panel(QObject):
        enabled_changed = Signal(bool)

        def __init__(self):
            super().__init__()
            self._closed = False
            self._enabled = False
            self._requested_enabled = False
            self._pending_enabled = False

    panel = Panel()
    bridge = _Bridge(panel)
    # Native has queued the user's disable while an older runJavaScript call
    # remains in flight. Its old enable acknowledgment arrives before _sent.
    bridge.audioState(True)
    assert panel._pending_enabled is False
    assert panel._requested_enabled is False


def test_stopped_loop_cursor_matches_the_raw_transport_position():
    script = r"""
      const fs=require('fs'), vm=require('vm'), window={};
      const text=fs.readFileSync(process.argv[1], 'utf8');
      vm.runInNewContext(text.split('/* Local sound generation.')[0], {window});
      const sample={bar:1,beat:1,division:1,tick:0,meter:'4/4',rate:2,
        playing:false,sampleTime:1000,
        playback:{endBeat:64,loop:{startBeat:4,endBeat:12,iteration:0}}};
      console.log(JSON.stringify(window.ChordCueTimeline.position(sample,1000,{clampToBar:true})));
    """
    node = shutil.which("node")
    assert node, "Node.js is required for the shared timeline regression"
    resource = Path(__file__).resolve().parents[2] / "Resources" / "Metronome.js"
    result = subprocess.run([node, "-e", script, str(resource)], capture_output=True,
                            text=True, encoding="utf-8", timeout=10, check=True)
    cursor = json.loads(result.stdout)
    assert cursor["bar"] == 1
    assert cursor["offset"] == 0


def test_unrepresentable_project_size_is_rejected_before_replacing_ui(window, tmp_path):
    path = tmp_path / "oversized.chordcue.json"
    save_project(ChartDocument(), path)
    data = json.loads(path.read_text("utf-8"))
    data["bars"] = 2**31
    path.write_text(json.dumps(data), "utf-8")
    previous = window.document
    assert not window.open_project_path(path)
    assert window.document == previous


def test_stale_audio_sequence_cannot_reenable_after_pending_disable_was_sent():
    class Panel(QObject):
        enabled_changed = Signal(bool)

        def __init__(self):
            super().__init__()
            self._closed = False
            self._enabled = False
            self._requested_enabled = False
            self._pending_enabled = None
            self._command_sequence = 3

        def page(self):
            raise AssertionError("A stale audio acknowledgment must not unmute the page")

    owner = Panel()
    bridge = _Bridge(owner)
    bridge.audioState(True, 2)
    assert not owner._enabled
    assert not owner._requested_enabled
    assert owner._pending_enabled is None


def test_rapid_native_audio_commands_survive_delayed_resume_and_page_cycle(panel, qtbot):
    # Exercise the actual Qt page and QWebChannel without opening an output
    # device. The delayed fake resume forces a pending JavaScript activation.
    javascript(qtbot, panel, r"""(window.AudioContext=class {
      constructor(){this.state='suspended';this.sampleRate=48000;this.destination={};}
      resume(){return new Promise(resolve=>{window.__qaResume=()=>{this.state='running';resolve()}});}
      suspend(){this.state='suspended';return Promise.resolve();}
      close(){this.state='closed';return Promise.resolve();}
      createGain(){return {gain:{value:0,setTargetAtTime(){}},connect(){}};}
      createBuffer(c,n){return {getChannelData(){return new Float32Array(n)}};}
    },true)""")
    panel.update_transport(StandaloneTransport(ChartDocument()).snapshot(), "QA silent bridge")
    panel.set_enabled(False)
    panel.set_enabled(True)
    panel.set_enabled(False)
    qtbot.waitUntil(lambda: not panel._sending and panel._pending_enabled is None)
    assert not panel._requested_enabled
    assert not panel._enabled

    panel.set_enabled(True)
    qtbot.waitUntil(lambda: javascript(qtbot, panel,
                    "document.getElementById('audioEnable').disabled") is True)
    panel.set_enabled(False)
    original_page = panel.page()
    panel.hide()
    panel.show()
    qtbot.waitUntil(lambda: not panel._sending and panel._pending_enabled is None)
    javascript(qtbot, panel, "(window.__qaResume(),true)")
    assert panel.page() is original_page
    assert not panel._enabled and not panel._requested_enabled
    status = javascript(qtbot, panel,
                        "({label:document.getElementById('audioEnable').textContent,"
                        "disabled:document.getElementById('audioEnable').disabled,"
                        "scheduled:ChordCueMetronome.diagnostics().scheduled})")
    assert status == {"label": "开启声音", "disabled": False, "scheduled": 0}


def test_reloaded_page_audio_notifications_use_the_current_native_sequence(panel, qtbot):
    panel.set_enabled(False)
    qtbot.waitUntil(lambda: not panel._sending and panel._pending_enabled is None)
    assert panel._command_sequence > 0
    with qtbot.waitSignal(panel.page().loadFinished, timeout=20_000):
        panel.page().triggerAction(QWebEnginePage.WebAction.Reload)
    qtbot.waitUntil(lambda: panel.ready and not panel._sending, timeout=20_000)
    # A fresh page's notification must reach the native checkbox. This calls
    # only the reporting bridge; no actual AudioContext or speaker is enabled.
    javascript(qtbot, panel, "(ChordCueBridge.audioState(true),true)")
    qtbot.waitUntil(lambda: panel._enabled, timeout=1_000)


def test_unpaginatable_pdf_does_not_destroy_existing_export(qapp, tmp_path):
    path = tmp_path / "existing.pdf"
    export_pdf(ChartDocument(bars=1), path)
    original = path.read_bytes()
    document = ChartDocument(bars=1, forced_key=MusicalKey(0),
                             events=(ChordEvent(0, 1, 0, "C" + "add9" * 300),))
    with pytest.raises(ValueError, match="名称过长"):
        export_pdf(document, path)
    assert path.read_bytes() == original
