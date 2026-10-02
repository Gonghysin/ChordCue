"""Opt-in, bounded startup diagnostics; not a real-device audio acceptance test."""

from pathlib import Path
import json
import platform
import sys

from PySide6.QtCore import QTimer, qVersion


def run_smoke(window, output_directory: Path, app) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": False,
        "python": platform.python_version(),
        "qt": qVersion(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "physical_audio_verified": False,
        "cross_device_lan_verified": False,
    }
    poll = QTimer(window)
    watchdog = QTimer(window)
    watchdog.setSingleShot(True)
    finished = False

    def finish(error=None):
        nonlocal finished
        if finished:
            return
        finished = True
        poll.stop()
        watchdog.stop()
        if error:
            report["error"] = str(error)
        else:
            report["passed"] = True
        (output_directory / "smoke.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.close()
        app.exit(0 if report["passed"] else 1)

    def verify_audio(result):
        if finished:
            return
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                return
        if not isinstance(result, dict) or not result.get("done"):
            return
        if result.get("error"):
            finish(result["error"])
            return
        try:
            peak = result.get("peak", 0)
            if not 0.5 < peak <= 1.01:
                raise RuntimeError(f"Offline Web Audio peak is invalid: {peak}")
            report["offline_web_audio"] = result
            window.export_chart_pdf(output_directory / "demo.pdf")
            if (output_directory / "demo.pdf").stat().st_size < 1000:
                raise RuntimeError("PDF output is empty")
            if not window.grab().save(str(output_directory / "window.png")):
                raise RuntimeError("Could not save the application screenshot")
            report["pdf_created"] = True
            report["window_captured"] = True
            finish()
        except Exception as error:
            finish(error)

    started_audio = False

    def tick():
        nonlocal started_audio
        try:
            if not window.audio.ready:
                return
            if not started_audio:
                started_audio = True
                # OfflineAudioContext proves synthesis without enabling real speakers.
                window.audio.page().runJavaScript("""
                    window.__chordcueSmoke = {done:false};
                    (async()=>{try {
                      const c=new OfflineAudioContext(1,4800,48000);
                      const o=c.createOscillator();o.frequency.value=880;
                      o.connect(c.destination);o.start(0);o.stop(0.05);
                      const b=await c.startRendering();let peak=0;
                      for(const x of b.getChannelData(0))peak=Math.max(peak,Math.abs(x));
                      window.__chordcueSmoke={done:true,peak,frames:b.length,sampleRate:b.sampleRate};
                    }catch(e){window.__chordcueSmoke={done:true,error:String(e)}}})();
                """)
            window.audio.page().runJavaScript("JSON.stringify(window.__chordcueSmoke)", verify_audio)
        except Exception as error:
            finish(error)

    poll.timeout.connect(tick)
    watchdog.timeout.connect(lambda: finish("WebEngine startup timed out after 30 seconds"))
    poll.start(100)
    watchdog.start(30000)
