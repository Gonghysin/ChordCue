"""Exercise the actual offline WebEngine parser and SVG notation renderer."""
import json
from copy import deepcopy
from pathlib import Path

from chordcue.score_panel import ScorePanel
from chordcue.score_models import ScoreIR


FIXTURES = Path(__file__).parent / "fixtures"


def js(qtbot, panel, expression):
    values = []
    panel._page.runJavaScript("JSON.stringify("+expression+")", values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=5000)
    return json.loads(values[0])


def test_offline_import_renders_staff_and_tab_and_preserves_previous_on_error(qtbot):
    panel = ScorePanel()
    qtbot.addWidget(panel)
    panel.resize(950, 740)
    panel.show()
    qtbot.waitUntil(lambda: panel.ready, timeout=20000)
    results, failures = [], []
    panel.import_file(FIXTURES / "issue1-original.musicxml", results.append, failures.append)
    qtbot.waitUntil(lambda: bool(results or failures), timeout=20000)
    assert not failures
    score = results[0]
    assert len(score.parts) == 3
    for kind in ("staff", "tab"):
        panel.show_score(score, "p1", kind)
        qtbot.waitUntil(lambda: js(qtbot, panel, "view?.rendered===true && view?.view==='"+kind+"' && document.querySelectorAll('#view svg').length>0"),
                        timeout=20000)
        assert js(qtbot, panel, "document.getElementById('error').textContent") == ""
        assert "吉他" in js(qtbot, panel, "document.querySelector('.score-view-heading').textContent")
    panel.import_file(FIXTURES / "issue1-original.gp", results.append, failures.append)
    qtbot.waitUntil(lambda: len(results) == 2 or bool(failures), timeout=20000)
    assert not failures
    assert results[1].source.format == "guitarpro"
    panel.import_file(FIXTURES / "missing.musicxml", results.append, failures.append)
    assert failures
    assert len(results) == 2
    assert js(qtbot, panel, "document.querySelectorAll('#view svg').length") > 0
    seeks = []
    panel.seek_requested.connect(lambda *position: seeks.append(position))
    qtbot.waitUntil(lambda: js(qtbot, panel, "!!window.ChordCueScoreBridge"), timeout=5000)
    assert js(qtbot, panel, "(()=>{window.ChordCueScoreBridge.seek('m1',1.5);return true})()")
    qtbot.waitUntil(lambda: bool(seeks), timeout=5000)
    assert seeks == [("m1", 1.5)]
    panel.clear_score("请选择已知调性")
    assert js(qtbot, panel, "view===null && document.querySelectorAll('#view svg').length===0")
    assert panel._latest is None
    assert js(qtbot, panel, "document.getElementById('error').textContent") == "请选择已知调性"
    assert js(qtbot, panel, "(()=>{window.ChordCueScoreBridge.seek('m1',1.5);return true})()")
    qtbot.wait(50)
    assert seeks == [("m1", 1.5)]
    panel.show_score(score, "p2", "staff")
    qtbot.waitUntil(lambda: js(qtbot, panel, "view?.rendered===true && view?.partId==='p2'"), timeout=20000)
    panel.shutdown()


def multi_row_score():
    raw = json.loads((FIXTURES / "issue1-original.score.json").read_text("utf-8"))
    measure = deepcopy(raw["measures"][0])
    part = deepcopy(raw["parts"][0])
    staff = deepcopy(part["staves"][0])
    originals = [e for e in staff["events"] if e["measureId"] == measure["id"]]
    raw["measures"] = []
    staff["events"] = []
    for index in range(24):
        current = deepcopy(measure)
        current.update(id=f"row.m{index}", number=str(index+1), repeatStart=False,
                       repeatEnd=None, endingNumbers=[], markers=[], navigation=[])
        raw["measures"].append(current)
        for event_index, original in enumerate(originals):
            event = deepcopy(original)
            event.update(id=f"row.e{index}.{event_index}", measureId=current["id"])
            for note_index, note in enumerate(event["notes"]):
                note["id"] = f"row.n{index}.{event_index}.{note_index}"
            staff["events"].append(event)
    sustained = deepcopy(staff["events"][0])
    sustained.update(id="row.long", voice=2, duration={"numerator": 4, "denominator": 1})
    for i, note in enumerate(sustained["notes"]):
        note["id"] = f"row.long.n{i}"
    staff["events"].append(sustained)
    part.update(staves=[staff], chords=[])
    raw.update(parts=[part], tempoChanges=[], keyChanges=[], warnings=[])
    return ScoreIR.from_dict(raw)


def test_actual_note_boxes_system_top_last_row_and_follow_toggle(qtbot, tmp_path):
    panel = ScorePanel()
    qtbot.addWidget(panel)
    panel.resize(680, 500)
    panel.show()
    qtbot.waitUntil(lambda: panel.ready, timeout=20000)
    score = multi_row_score()
    for kind in ("staff", "tab"):
        panel.show_score(score, "p1", kind)
        qtbot.waitUntil(lambda: js(qtbot, panel, "view?.rendered===true && view?.view==='"+kind+"'"), timeout=20000)
        panel.set_position({"sourceMeasureId": "row.m0", "sourceOffsetQuarter": 0,
                            "playing": True, "valid": True})
        assert js(qtbot, panel, "view.highlights.filter(x=>!x.hidden).length") >= 2
        assert js(qtbot, panel, "view.highlights.filter(x=>!x.hidden).every(x=>parseFloat(x.style.width)>6 && parseFloat(x.style.width)<50 && parseFloat(x.style.height)<50)")
        for last in (False, True):
            expression = """(() => {
                const entries=[...view.converted.beatMap].flatMap(([id,items])=>items.map(e=>({id,e,b:view.api.renderer.boundsLookup.findBeat(e.beat)}))).filter(x=>x.b);
                const index=%s;
                const target=entries.find(x=>x.b.barBounds.masterBarBounds.staffSystemBounds.index===index);
                return {id:target.id,offset:target.e.start,system:index};
            })()""" % ("view.api.renderer.boundsLookup.staffSystems.length-1" if last else "1")
            target = js(qtbot, panel, expression)
            panel.set_position({"sourceMeasureId": target["id"], "sourceOffsetQuarter": target["offset"],
                                "playing": True, "valid": True})
            assert abs(js(qtbot, panel, "view.element.getBoundingClientRect().top+view._origin().y+view.api.renderer.boundsLookup.staffSystems["+str(target["system"])+"].realBounds.y")) <= 8
        panel.set_follow(False)
        js(qtbot, panel, "(()=>{window.scrollTo(0,123);return true})()")
        panel.set_position({"sourceMeasureId": "row.m0", "sourceOffsetQuarter": 0, "playing": True})
        assert abs(js(qtbot, panel, "window.scrollY") - 123) < 1
        assert js(qtbot, panel, "view.highlights.some(x=>!x.hidden)")
        panel.set_position(None)
        assert js(qtbot, panel, "view.highlights.every(x=>x.hidden)")
        panel.set_follow(True)
    panel.set_position({"sourceMeasureId": "row.m0", "sourceOffsetQuarter": 0, "playing": True})
    js(qtbot, panel, "view.highlights.some(x=>!x.hidden)")
    panel.view.grab().save(str(tmp_path / "note-boxes.png"))
    panel.shutdown()


def test_imported_guitar_techniques_render_in_real_svg(qtbot, tmp_path):
    panel = ScorePanel()
    qtbot.addWidget(panel)
    panel.resize(960, 720)
    panel.show()
    qtbot.waitUntil(lambda: panel.ready, timeout=20000)
    for source in ("gp", "xml"):
        score = ScoreIR.from_dict(json.loads((FIXTURES / f"techniques-{source}.score.json").read_text("utf-8")))
        for kind in ("staff", "tab"):
            panel.show_score(score, "p1", kind)
            qtbot.waitUntil(lambda: js(qtbot, panel, "view?.rendered===true && view?.view==='"+kind+"' && document.querySelectorAll('#view svg').length>0"), timeout=20000)
            assert js(qtbot, panel, "document.getElementById('error').textContent") == ""
            assert js(qtbot, panel, "document.querySelectorAll('#view svg').length") > 0
            panel.view.grab().save(str(tmp_path / f"techniques-{source}-{kind}.png"))
    panel.shutdown()
