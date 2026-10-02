from dataclasses import fields, replace
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from chordcue.models import ChartDocument, document_with_score
from chordcue.score_models import (
    QuarterFraction as Q, ScoreBendPoint, ScoreTechnique, ScoreChord, ScoreEvent, ScoreIR, ScoreKeyChange,
    ScoreMarker, ScoreMeasure, ScoreMeter, ScoreNavigation, ScoreNote, ScorePart,
    ScoreSource, ScoreStaff, ScoreTempoChange, ScoreWarning, check_json_complexity,
)


def sample_score():
    """A pickup, exact triplet, grace note, TAB/rest, maps and endings/jump."""
    return ScoreIR(
        id="score1", title="原创 · TAB/五线谱", source=ScoreSource("musicxml", "练习.musicxml", "a" * 64),
        measures=(
            ScoreMeasure("m0", "0", Q(1, 2)),
            ScoreMeasure("m1", "1a", Q(3), ScoreMeter(6, 8), repeat_start=True,
                         repeat_end=2, ending_numbers=(1,),
                         markers=(ScoreMarker("segno1", "segno", "Segno", Q(0)),)),
            ScoreMeasure("m2", "2", Q(4), ending_numbers=(2,),
                         navigation=(ScoreNavigation("ds", "segno1", Q(4)),)),
        ),
        parts=(ScorePart("p1", "吉他", "Acoustic guitar", staves=(
            ScoreStaff("staff1", "TAB", "tab", "TAB", (64, 59, 55, 50, 45, 40), 2, (
                ScoreEvent("e0", "m0", Q(0), Q(1, 2), notes=(
                    ScoreNote("n0", 64, 1, 0, tie_start=True, accidental="natural", written_pitch=64,
                              techniques=(ScoreTechnique("hammerOn", target_note_id="n1"),)),)),
                ScoreEvent("e1", "m1", Q(1, 3), Q(1, 3), notes=(
                    ScoreNote("n1", 66, 1, 2, tie_stop=True, accidental="sharp", written_pitch=66,
                              techniques=(ScoreTechnique("bend", direction="release", curve=(
                                  ScoreBendPoint(0, 0), ScoreBendPoint(.5, 2), ScoreBendPoint(1, 0))),)),
                    ScoreNote("n2", 61, 2, 2),), techniques=(ScoreTechnique("pick", direction="down"),)),
                ScoreEvent("eg", "m1", Q(1, 3), Q(0), grace=True, notes=(ScoreNote("ng", 67),)),
                ScoreEvent("er", "m1", Q(2, 3), Q(7, 3), is_rest=True),
            )),
        ), chords=(ScoreChord("h1", "m1", Q(1, 3), "F♯m(add11)/C♯", "staff1"),)),
            ScorePart("p2", "钢琴", staves=(ScoreStaff("staff2", "Treble", events=(
                ScoreEvent("ep", "m2", Q(0), Q(4), notes=(ScoreNote("np", 60),)),
            )),)),
        ),
        tempo_changes=(ScoreTempoChange("m0", Q(0), 93.75), ScoreTempoChange("m1", Q(0), 77.5),
                       ScoreTempoChange("m2", Q(0), 165)),
        key_changes=(ScoreKeyChange("m0", Q(0), 1, "minor"), ScoreKeyChange("m2", Q(0), -2, "major")),
        warnings=(ScoreWarning("capo-source", "Capo follows source tuning metadata", "info", "m1", "p1"),),
    )


def test_score_roundtrip_retains_original_time_labels_maps_and_annotations():
    score = sample_score()
    raw = score.to_dict()
    restored = ScoreIR.from_dict(json.loads(json.dumps(raw, ensure_ascii=False)))
    assert restored == score
    assert raw["measures"][0]["number"] == "0"
    assert raw["measures"][1]["number"] == "1a"
    event = raw["parts"][0]["staves"][0]["events"][1]
    assert event["offset"] == {"numerator": 1, "denominator": 3}
    assert event["notes"][0]["tieStop"] is True
    assert raw["parts"][0]["staves"][0]["tuning"] == [64, 59, 55, 50, 45, 40]
    assert raw["parts"][0]["chords"][0]["text"] == "F♯m(add11)/C♯"
    assert raw["measures"][2]["navigation"][0]["targetMarkerId"] == "segno1"
    assert restored.tempo_changes[1].bpm == 77.5
    assert restored.key_changes[-1].fifths == -2
    assert restored.warnings[0].part_id == "p1"


def test_notes_only_selection_adapter_preserves_score_and_does_not_infer_chords():
    source = sample_score()
    score = replace(source, parts=(source.parts[1],), warnings=())
    document = document_with_score(score)
    assert document.score is score
    assert document.events == ()
    assert score.parts[0].chords == ()
    assert document.bars == len(score.measures)
    assert document.selected_part_id == "p2"
    assert replace(document, name="Renamed").score is score
    with pytest.raises(ValueError, match="selected part"):
        document_with_score(score, "unknown")
    with pytest.raises(ValueError, match="requires a score"):
        ChartDocument(selected_part_id="p1")
    with pytest.raises(ValueError, match="measure count"):
        ChartDocument(score=score, bars=4)


@pytest.mark.parametrize("numerator,denominator", [
    (True, 1), (1, True), (1, 0), (1, -1), (1, 1_000_001), (2**31, 1), (1.5, 2),
])
def test_fraction_validation(numerator, denominator):
    with pytest.raises(ValueError):
        Q(numerator, denominator)


def test_fraction_reduction_and_exact_event_boundary():
    assert Q(24, 72) == Q(1, 3)
    assert Q(24, 72).as_fraction() == Fraction(1, 3)
    event = ScoreEvent("e", "m", Q(1, 3), Q(2, 3), notes=(ScoreNote("n", 60),))
    part = ScorePart("p", "P", staves=(ScoreStaff("st", events=(event,)),))
    score = ScoreIR("score", "Exact", ScoreSource("manual"), (ScoreMeasure("m", "1", Q(1)),), (part,))
    score.validate()
    with pytest.raises(ValueError, match="extends outside"):
        replace(score, measures=(replace(score.measures[0], duration=Q(999_999, 1_000_000)),))


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.update(formatVersion=True), "must be int"),
    (lambda d: d.update(formatVersion=3), "formatVersion"),
    (lambda d: d.update(unknownField="future"), "fields differ"),
    (lambda d: d["source"].pop("sha256"), "fields differ"),
    (lambda d: d["parts"][0]["staves"][0]["events"][1].update(measureId="missing"), "unknown source"),
    (lambda d: d["parts"][0]["chords"][0].update(staffId="staff2"), "must belong"),
    (lambda d: d["warnings"][0].update(partId="absent"), "unknown part"),
    (lambda d: d["measures"][2]["navigation"][0].update(targetMarkerId="absent"), "unknown marker"),
    (lambda d: d["tempoChanges"][0].update(bpm=float("inf")), "non-finite"),
    (lambda d: d["tempoChanges"].append(dict(d["tempoChanges"][0])), "duplicate tempo"),
    (lambda d: d["keyChanges"][0].update(fifths=8), "fifths"),
    (lambda d: d["parts"][0]["staves"][0]["events"][1]["notes"][0].update(id="n0"), "duplicate score"),
    (lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0].update(string=7), "outside staff tuning"),
    (lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0].update(fret=None), "together"),
    (lambda d: d["parts"][0]["staves"][0]["events"][0].update(isRest=True), "rest must"),
    (lambda d: d["parts"][0]["staves"][0]["events"][0].update(duration={"numerator": 0, "denominator": 1}), "quarter-note"),
    (lambda d: d["measures"][0].update(number="1\n2"), "single-line"),
])
def test_json_boundary_rejects_invalid_references_types_and_information_loss(mutate, match):
    raw = sample_score().to_dict()
    mutate(raw)
    with pytest.raises(ValueError, match=match):
        ScoreIR.from_dict(raw)


def test_unresolved_navigation_must_have_warning_and_can_be_previewed():
    score = sample_score()
    last = replace(score.measures[-1], navigation=(ScoreNavigation("toCoda", None, Q(4)),))
    with pytest.raises(ValueError, match="requires a measure warning"):
        replace(score, measures=(*score.measures[:-1], last))
    warning = ScoreWarning("navigation-unresolved", "Source coda target is ambiguous", measure_id=last.id)
    unresolved = replace(score, measures=(*score.measures[:-1], last), warnings=(*score.warnings, warning))
    assert ScoreIR.from_dict(unresolved.to_dict()) == unresolved


def test_total_budget_and_json_depth_are_bounded_before_import(monkeypatch):
    score = sample_score()
    monkeypatch.setattr("chordcue.score_models.MAX_JSON_NODES", 8)
    with pytest.raises(ValueError, match="complexity"):
        ScoreIR.from_dict(score.to_dict())
    monkeypatch.setattr("chordcue.score_models.MAX_JSON_NODES", 2_000_000)
    nested = 0
    for _ in range(34):
        nested = [nested]
    with pytest.raises(ValueError, match="complexity"):
        check_json_complexity(nested)
    monkeypatch.setattr("chordcue.score_models.MAX_EVENTS", 1)
    part = score.parts[1]
    second = replace(part, id="p3", staves=(replace(part.staves[0], id="staff3", events=(
        replace(part.staves[0].events[0], id="event3", notes=(ScoreNote("note3", 62),)),)),))
    with pytest.raises(ValueError, match="total event"):
        replace(score, parts=(part, second), warnings=())


def test_schema_field_names_remain_in_sync_with_typed_model():
    schema = json.loads((Path(__file__).parents[2] / "Resources/score/score.schema.json").read_text("utf-8"))
    import chordcue.score_models as models
    for name, definition in schema["$defs"].items():
        expected = {f.name.split("_")[0] + "".join(s.capitalize() for s in f.name.split("_")[1:])
                    for f in fields(getattr(models, name))}
        assert set(definition["properties"]) == expected
        assert set(definition["required"]) == expected
        assert definition["additionalProperties"] is False


@pytest.fixture(scope="module")
def swift_score_oracle(tmp_path_factory):
    compiler = shutil.which("swiftc")
    if compiler is None:
        pytest.skip("Swift compiler unavailable; macOS CI verifies shared ScoreIR codec")
    root = Path(__file__).parents[2]
    destination = tmp_path_factory.mktemp("score-swift") / "ScoreOracle"
    subprocess.run([compiler, "-parse-as-library", str(root / "Sources/ScoreModels.swift"),
                    str(Path(__file__).parent / "fixtures/ScoreModelsOracle.swift"), "-o", str(destination)],
                   check=True, capture_output=True, text=True)
    return destination


def test_swift_roundtrip_matches_python_exactly(swift_score_oracle):
    raw = sample_score().to_dict()
    result = subprocess.run([str(swift_score_oracle)], input=json.dumps(raw, ensure_ascii=False),
                            text=True, capture_output=True, check=True)
    assert ScoreIR.from_dict(json.loads(result.stdout)) == sample_score()


@pytest.mark.parametrize("invalid", ["duplicate", "fraction", "unknown", "bad-reference", "float-integer", "bool-integer"])
def test_swift_rejects_the_same_invalid_contract(swift_score_oracle, invalid):
    raw = sample_score().to_dict()
    if invalid == "fraction":
        raw["measures"][0]["duration"]["denominator"] = 0
    elif invalid == "unknown":
        raw["parts"][0]["future"] = "metadata would be lost"
    elif invalid == "bad-reference":
        raw["parts"][0]["staves"][0]["events"][0]["measureId"] = "absent"
    elif invalid == "float-integer":
        raw["formatVersion"] = 2.0
    elif invalid == "bool-integer":
        raw["measures"][0]["meter"]["numerator"] = True
    content = json.dumps(raw)
    if invalid == "duplicate":
        content = content.replace('"formatVersion": 2', '"formatVersion": 3, "formatVersion": 2')
    result = subprocess.run([str(swift_score_oracle)], input=content, text=True, capture_output=True)
    assert result.returncode != 0
    assert result.stderr.strip()


def test_v1_score_strict_migration_keeps_source_and_does_not_invent_lost_techniques():
    path = Path(__file__).parent / "fixtures/issue1-original.score.json"
    raw = json.loads(path.read_text("utf-8"))
    restored = ScoreIR.from_dict(raw)
    assert restored.format_version == 2
    assert raw["formatVersion"] == 1
    assert restored.parts[0].staves[0].events[0].notes[0].pitch == 64
    assert all(not note.techniques and note.written_pitch is None
               for part in restored.parts for staff in part.staves for event in staff.events for note in event.notes)
    raw["parts"][0]["staves"][0]["events"][0]["notes"][0]["techniques"] = []
    with pytest.raises(ValueError, match="fields differ"):
        ScoreIR.from_dict(raw)


@pytest.mark.parametrize("mutate", [
    lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0]["techniques"][0].update(targetNoteId="absent"),
    lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0]["techniques"][0].update(targetNoteId="n0"),
    lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0]["techniques"][0].update(targetNoteId="np"),
    lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0]["techniques"][0].update(targetNoteId="n2"),
    lambda d: d["parts"][0]["staves"][0]["events"][1]["notes"][0]["techniques"][0]["curve"][1].update(position=2),
    lambda d: d["parts"][0]["staves"][0]["events"][1]["notes"][0]["techniques"][0]["curve"][1].update(semitones=25),
    lambda d: d["parts"][0]["staves"][0]["events"][1]["notes"][0]["techniques"][0]["curve"][0].update(position=.1),
    lambda d: d["parts"][0]["staves"][0]["events"][0]["notes"][0].update(techniques=[
        {"kind": "futureTechnique", "targetNoteId": None, "value": None, "direction": None, "curve": []}]),
    lambda d: d["parts"][0]["staves"][0]["events"][0].update(techniques=[
        {"kind": "deadNote", "targetNoteId": None, "value": None, "direction": None, "curve": []}]),
])
def test_v2_rejects_damaged_links_curves_and_unknown_or_misplaced_techniques(mutate):
    raw = sample_score().to_dict()
    mutate(raw)
    with pytest.raises(ValueError):
        ScoreIR.from_dict(raw)


def test_technique_budgets_and_conditional_values_are_enforced():
    with pytest.raises(ValueError):
        ScoreTechnique("pick", direction="up", value=1)
    with pytest.raises(ValueError):
        ScoreTechnique("bend", direction="bend", curve=(ScoreBendPoint(0, 0),) * 65)
    with pytest.raises(ValueError):
        ScoreTechnique("trill", value=66.5)
    with pytest.raises(ValueError):
        ScoreTechnique("tremoloPicking", value=12)
    with pytest.raises(ValueError):
        ScoreTechnique("brush", direction="up", value=17)
    with pytest.raises(ValueError, match="event technique"):
        ScoreNote("bad", 60, techniques=(ScoreTechnique("brush", direction="up"),))
    with pytest.raises(ValueError):
        replace(sample_score().parts[0].staves[0].events[0].notes[0], techniques=(ScoreTechnique("deadNote"),) * 33)


@pytest.mark.parametrize("fixture", ["techniques-xml.score.json", "techniques-gp.score.json", "issue1-original.score.json"])
def test_swift_v2_techniques_and_v1_migration_match_python(swift_score_oracle, fixture):
    raw = json.loads((Path(__file__).parent / "fixtures" / fixture).read_text("utf-8"))
    result = subprocess.run([str(swift_score_oracle)], input=json.dumps(raw, ensure_ascii=False),
                            text=True, capture_output=True, check=True)
    assert ScoreIR.from_dict(json.loads(result.stdout)) == ScoreIR.from_dict(raw)
