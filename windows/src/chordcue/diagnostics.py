"""Opt-in, bounded startup diagnostics; not a real-device audio acceptance test."""

from pathlib import Path
import json
import platform
import sys

from PySide6.QtCore import QTimer, qVersion


def run_smoke(window, output_directory: Path, app, *, score_path: Path | None = None) -> None:
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
        window._saved_document = window.document
        window._editor_dirty = False
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
    score_stage = "new" if score_path is not None else "done"
    score_check_busy = False

    def score_imported(score):
        nonlocal score_stage
        from .models import document_with_score
        from .transport import StandaloneTransport
        window._replace_document(document_with_score(score))
        report["source_timing_preserved"] = window.document.score is score
        # Advance a deterministic clock across original tempo boundaries. The
        # packaged importer and transport run without any timing overrides.
        clock = [0]
        probe = StandaloneTransport(window.document, lambda: clock[0])
        samples = []
        try:
            assert probe.plan is not None
            probe.play()
            for segment in probe.plan.segments:
                clock[0] = 400_000_000 + round(segment.start_seconds * 1_000_000_000) + 1
                sample = probe.snapshot(include_route=False)
                meter = score.measures[sample["bar"]-1].meter
                if sample["bpm"] != segment.bpm or sample["meter"] != f"{meter.numerator}/{meter.denominator}":
                    raise RuntimeError("Imported timing failed its automatic playback check")
                samples.append({"bar": sample["bar"], "bpm": sample["bpm"], "meter": sample["meter"]})
        finally:
            probe.close()
        report["source_timing_samples"] = samples
        report["source_automatic_timing_verified"] = True
        report["score_format"] = score.source.format
        report["score_parts"] = len(score.parts)
        report["score_route_occurrences"] = len(window.transport.plan.occurrences)
        score_stage = "staff"
        window.tabs.setCurrentWidget(window.score_page)

    def check_score_render(result):
        nonlocal score_stage, score_check_busy
        score_check_busy = False
        try:
            state = json.loads(result)
            if state.get("error"):
                finish(state["error"])
                return
            if not state.get("rendered") or not state.get("svg"):
                return
            report[score_stage+"_svg_rendered"] = True
            if score_stage == "staff":
                window.score_kind.setCurrentIndex(1)
                score_stage = "tab"
            else:
                window.transport.seek_quarter(min(1.5, float(window.transport.plan.end_quarter)))
                window._tick()
                report["score_seek_quarter"] = window.transport.snapshot()["playQuarter"]
                score_stage = "done"
        except (TypeError, ValueError) as error:
            finish(error)

    def tick():
        nonlocal started_audio, score_stage, score_check_busy
        try:
            if score_stage == "new":
                score_stage = "loading"
                window._ensure_score_panel().import_file(score_path, score_imported, finish)
            if score_stage in ("staff", "tab") and not score_check_busy:
                score_check_busy = True
                window.score_panel._page.runJavaScript("JSON.stringify({rendered:view?.rendered===true,svg:document.querySelectorAll('#view svg').length,error:document.getElementById('error').textContent})", check_score_render)
            if score_stage != "done":
                return
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
    watchdog.timeout.connect(lambda: finish(f"Smoke timed out after 30 seconds (score stage: {score_stage})"))
    if score_path is None:
        try:
            from .models import ChartDocument, ChordEvent, TimingChange
            from .project import load_project, save_project
            rows = (TimingChange(1, 120, 4, 4), TimingChange(2, 90, 6, 8), TimingChange(3, 150, 7, 8))
            window._replace_document(ChartDocument(name="既有变拍工程兼容", bars=8, timing_changes=rows,
                events=(ChordEvent(0, 1, 0, "C"), ChordEvent(1, 2, 2400, "G"),
                        ChordEvent(2, 3, 3120, "Am"))))
            project = output_directory / "legacy-timing.chordcue.json"
            save_project(window.document, project)
            if load_project(project) != window.document:
                raise RuntimeError("Timing project failed its save/reopen check")
            timing_samples = []
            for bar in (1, 2, 3):
                window._seek(bar, 1)
                sample = window.transport.snapshot()
                timing_samples.append({"bar": sample["bar"], "bpm": sample["bpm"],
                                       "meter": sample["meter"]})
            report["legacy_timing_samples"] = timing_samples
            report["legacy_timing_save_reopen_verified"] = True
        except Exception as error:
            finish(error)
            return
    poll.start(100)
    watchdog.start(30000)
