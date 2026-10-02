"""Isolated frozen Qt/WebEngine/WebAudio/PDF proof, with no audible output."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QPainter, QPdfWriter
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication


def main() -> int:
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("ChordCue Packaging Probe")
    result = {"frozen": bool(getattr(sys, "frozen", False)), "webengine": False}
    writer = QPdfWriter(str(output / "probe.pdf"))
    painter = QPainter(writer)
    painter.drawText(150, 300, "ChordCue — Qt PDF probe")
    painter.end()
    result["pdf"] = (output / "probe.pdf").read_bytes().startswith(b"%PDF-")
    view = QWebEngineView()
    view.setWindowTitle("ChordCue packaging smoke test")
    view.resize(640, 400)

    def finish(code: int) -> None:
        (output / "probe.json").write_text(json.dumps(result, indent=2), "utf-8")
        view.close()
        app.exit(code)

    def poll() -> None:
        def received(data: str) -> None:
            parsed = json.loads(data) if data else None
            if parsed is None:
                QTimer.singleShot(100, poll)
                return
            result.update(parsed)
            finish(0 if result.get("webengine") and result.get("offline_audio") else 1)
        view.page().runJavaScript("JSON.stringify(window.probeResult || null)", received)

    def loaded(ok: bool) -> None:
        if not ok:
            result["error"] = "WebEngine page load failed"
            finish(1)
        else:
            poll()

    view.loadFinished.connect(loaded)
    view.setHtml("""<!doctype html><meta charset=utf-8><h1>ChordCue packaging probe</h1>
<p>Offline WebAudio rendering does not play sound.</p><script>
(async () => {
  try {
    const ctx = new OfflineAudioContext(1, 4096, 44100);
    const tone = ctx.createOscillator(); tone.connect(ctx.destination); tone.start();
    const buffer = await ctx.startRendering();
    const energy = buffer.getChannelData(0).reduce((sum, x) => sum + x * x, 0);
    window.probeResult = {webengine: true, offline_audio: energy > 1,
      audio_context_available: typeof AudioContext === 'function', energy};
  } catch (error) { window.probeResult = {webengine: true, error: String(error)}; }
})();</script>""", QUrl("file:///chordcue-probe/"))
    view.show()
    QTimer.singleShot(30000, lambda: (result.update(error="probe timeout"), finish(2)))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
