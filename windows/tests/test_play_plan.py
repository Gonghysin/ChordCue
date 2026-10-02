from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from chordcue.models import LoopRange, document_with_score
from chordcue.play_plan import PlayPlan
from chordcue.score_models import (
    QuarterFraction as Q, ScoreIR, ScoreMarker, ScoreMeasure, ScoreMeter,
    ScoreNavigation, ScorePart, ScoreSource, ScoreStaff, ScoreTempoChange, ScoreWarning,
)
from chordcue.transport import StandaloneTransport
from test_transport import FakeClock


def route_score(measures=None, tempos=None, warnings=()):
    return ScoreIR("score-route", "Route", ScoreSource("manual"), tuple(measures or (
        ScoreMeasure("m0", "0", Q(1, 2)),
        ScoreMeasure("m1", "1", Q(3), ScoreMeter(6, 8), repeat_start=True),
        ScoreMeasure("m2", "2", Q(1), ScoreMeter(2, 4), repeat_end=2, ending_numbers=(1,)),
        ScoreMeasure("m3", "3", Q(2), ending_numbers=(2,)),
        ScoreMeasure("m4", "4", Q(4)),
    )), (ScorePart("p1", "Guitar", staves=(ScoreStaff("st1"),)),
          ScorePart("p2", "Piano", staves=(ScoreStaff("st2"),))),
        tempo_changes=tuple(tempos or (
            ScoreTempoChange("m0", Q(0), 60), ScoreTempoChange("m1", Q(0), 120),
            ScoreTempoChange("m1", Q(3, 2), 90), ScoreTempoChange("m2", Q(0), 180),
            ScoreTempoChange("m3", Q(0), 60), ScoreTempoChange("m4", Q(0), 240),
        )), warnings=warnings)


def test_repeat_endings_pickup_and_source_identity_are_exact():
    plan = PlayPlan(route_score())
    assert [item.source_measure_id for item in plan.occurrences] == ["m0", "m1", "m2", "m1", "m3", "m4"]
    assert [item.id for item in plan.occurrences] == ["m0@1", "m1@1", "m2@1", "m1@2", "m3@1", "m4@1"]
    assert plan.occurrences[0].end_quarter == Fraction(1, 2)
    assert plan.end_quarter == Fraction(27, 2)
    assert plan.source_position(2, 0, occurrence=2) == 4.5
    assert plan.source_loop_bounds(2, 4) == (0.5, 7.5)
    assert plan.to_dict()["occurrences"][1]["meter"] == {"numerator": 6, "denominator": 8}


def test_nested_and_implicit_repeats_have_bounded_deterministic_occurrences():
    measures = (ScoreMeasure("m0", "1", Q(1), repeat_start=True),
                ScoreMeasure("m1", "2", Q(1), repeat_start=True),
                ScoreMeasure("m2", "3", Q(1), repeat_end=2),
                ScoreMeasure("m3", "4", Q(1), repeat_end=2))
    plan = PlayPlan(route_score(measures, [ScoreTempoChange("m0", Q(0), 120)]))
    assert [o.source_index for o in plan.occurrences] == [1, 2, 3, 2, 3, 4] * 2
    implicit = replace(measures[0], repeat_start=False)
    assert PlayPlan(route_score((implicit, ScoreMeasure("m1", "2", Q(1), repeat_end=3)),
                                [ScoreTempoChange("m0", Q(0), 120)])).end_quarter == 6


def test_tempo_map_integrates_and_repeated_source_restores_tempo():
    plan = PlayPlan(route_score())
    assert plan.time_at(0.5) == pytest.approx(0.5)
    assert plan.time_at(2) == pytest.approx(1.25)
    assert plan.time_at(3.5) == pytest.approx(2.25)
    assert plan.bpm_at(3.5) == 180
    assert plan.bpm_at(4.5) == 120
    for quarter in (0, 0.25, 0.5, 1.333333, 2, 4, 4.5, 7.5, 9.5, 13.5):
        assert plan.quarter_at(plan.time_at(quarter)) == pytest.approx(quarter, abs=1e-12)


def test_tempo_at_bar_end_carries_without_zero_duration_segments():
    score = route_score((ScoreMeasure("m0", "1", Q(1)), ScoreMeasure("m1", "2", Q(1))),
                        [ScoreTempoChange("m0", Q(0), 60), ScoreTempoChange("m0", Q(1), 120)])
    plan = PlayPlan(score)
    assert len(plan.segments) == 2
    assert all(s.end_quarter > s.start_quarter and s.end_seconds > s.start_seconds for s in plan.segments)
    assert plan.bpm_at(1) == 120
    assert plan.duration_seconds == 1.5


def test_dc_fine_and_ds_coda_navigation_are_bounded_and_source_preserving():
    measures = (ScoreMeasure("m0", "1", Q(1)),
                ScoreMeasure("m1", "2", Q(1), markers=(ScoreMarker("fine", "fine", "Fine", Q(1)),)),
                ScoreMeasure("m2", "3", Q(1), navigation=(ScoreNavigation("dc", None, Q(1)),)))
    plan = PlayPlan(route_score(measures, [ScoreTempoChange("m0", Q(0), 120)]))
    assert [o.source_index for o in plan.occurrences] == [1, 2, 3, 1, 2]
    measures = (ScoreMeasure("m0", "1", Q(1), markers=(ScoreMarker("segno", "segno"),)),
                ScoreMeasure("m1", "2", Q(1), navigation=(ScoreNavigation("toCoda", "coda", Q(1)),)),
                ScoreMeasure("m2", "3", Q(1), navigation=(ScoreNavigation("ds", "segno", Q(1)),)),
                ScoreMeasure("m3", "4", Q(1), markers=(ScoreMarker("coda", "coda"),)))
    plan = PlayPlan(route_score(measures, [ScoreTempoChange("m0", Q(0), 120)]))
    assert [o.source_index for o in plan.occurrences] == [1, 2, 3, 1, 2, 4]


def test_unsupported_navigation_is_visible_without_inventing_a_target():
    measures = (ScoreMeasure("m0", "1", Q(1)), ScoreMeasure("m1", "2", Q(1),
                navigation=(ScoreNavigation("ds", None, Q(1)), ScoreNavigation("dc", None, Q(1, 2)))))
    score = route_score(measures, [ScoreTempoChange("m0", Q(0), 120)],
                        warnings=(ScoreWarning("navigation-source", "Unresolved source segno", measure_id="m1"),))
    plan = PlayPlan(score)
    assert [o.source_index for o in plan.occurrences] == [1, 2]
    assert {w.code for w in plan.warnings} >= {"navigation-source", "navigation-unresolved", "navigation-mid-measure"}


def test_plan_json_copy_cannot_mutate_cached_route_or_source_score():
    score = route_score()
    plan = PlayPlan(score)
    wire = plan.to_dict()
    wire["occurrences"][0]["sourceMeasureId"] = "changed"
    assert plan.to_dict()["occurrences"][0]["sourceMeasureId"] == "m0"
    assert score.measures[0].id == "m0"


def test_route_occurrence_and_rational_limits_reject_before_user_state_changes(monkeypatch):
    monkeypatch.setattr("chordcue.play_plan.MAX_OCCURRENCES", 3)
    with pytest.raises(ValueError, match="occurrence"):
        PlayPlan(route_score())
    monkeypatch.setattr("chordcue.play_plan.MAX_OCCURRENCES", 100_000)
    measures = (ScoreMeasure("m0", "1", Q(1, 999_983)), ScoreMeasure("m1", "2", Q(1, 999_979)))
    with pytest.raises(ValueError, match="rational"):
        PlayPlan(route_score(measures, [ScoreTempoChange("m0", Q(0), 120)]))


def test_score_transport_preparation_tempo_boundaries_selection_seek_and_end():
    clock = FakeClock()
    engine = StandaloneTransport(document_with_score(route_score()), clock)
    initial = engine.snapshot()
    assert initial["sourceMeasureId"] == "m0" and initial["bar"] == 1
    engine.play()
    epoch = engine.snapshot()["discontinuity"]
    clock.advance(399_999_999)
    assert engine.snapshot()["playQuarter"] == 0 and engine.snapshot()["preparing"]
    clock.advance(500_000_001)
    sample = engine.snapshot()
    assert sample["playQuarter"] == 0.5 and sample["occurrenceId"] == "m1@1"
    assert sample["bpm"] == 120 and sample["meter"] == "6/8"
    engine.select_part("p2")
    assert engine.document.selected_part_id == "p2"
    assert engine.snapshot()["discontinuity"] == epoch
    assert engine.snapshot()["playQuarter"] == sample["playQuarter"]
    assert engine.snapshot()["routeId"] == sample["routeId"]
    engine.seek(2, 1 + 1 / 3, occurrence=2)
    assert engine.snapshot()["occurrenceId"] == "m1@2"
    assert engine.snapshot()["sourceOffsetQuarter"] == pytest.approx(1 / 3)
    engine.seek_quarter(13.5)
    assert not engine.snapshot()["playing"] and engine.snapshot()["bar"] == 6
    engine.play()
    assert engine.snapshot()["playQuarter"] == 0 and engine.snapshot()["preparing"]


def test_score_loops_use_tempo_seconds_and_keep_epoch_for_hundreds_of_cycles():
    clock = FakeClock()
    score = route_score((ScoreMeasure("m0", "1", Q(2)),),
                        [ScoreTempoChange("m0", Q(0), 60), ScoreTempoChange("m0", Q(1), 120)])
    engine = StandaloneTransport(replace(document_with_score(score), loop=LoopRange(1, 2)), clock)
    engine.play()
    clock.advance(400_000_000)
    epoch = engine.snapshot()["discontinuity"]
    for iteration in range(1, 101):
        clock.advance(1_500_000_000)
        sample = engine.snapshot()
        assert sample["playQuarter"] == 0
        assert sample["playback"]["loop"]["iteration"] == iteration
        assert sample["discontinuity"] == epoch
    clock.advance(1_250_000_000)
    assert engine.snapshot()["playQuarter"] == 1.5 and engine.snapshot()["bpm"] == 120
    engine.set_loop(None)
    assert engine.snapshot()["playQuarter"] == 1.5


def test_tempo_scaling_preserves_phase_and_source_maps():
    clock = FakeClock()
    engine = StandaloneTransport(document_with_score(route_score()), clock)
    engine.play()
    clock.advance(1_400_000_000)
    before = engine.snapshot()
    engine.set_bpm(engine.document.bpm * 2)
    after = engine.snapshot()
    assert after["playQuarter"] == before["playQuarter"]
    assert after["bpm"] == before["bpm"] * 2
    assert engine.document.score.tempo_changes[0].bpm == 60
    assert after["routeId"] != before["routeId"]
    with pytest.raises(ValueError, match="source map"):
        engine.set_meter(3)


@pytest.mark.parametrize("quarter", [True, float("nan"), float("inf"), -1, 100, 10**1000])
def test_invalid_score_seek_is_atomic(quarter):
    clock = FakeClock()
    engine = StandaloneTransport(document_with_score(route_score()), clock)
    before = engine.snapshot()
    with pytest.raises(ValueError):
        engine.seek_quarter(quarter)
    assert engine.snapshot() == before


@pytest.fixture(scope="module")
def swift_play_oracle(tmp_path_factory):
    compiler = shutil.which("swiftc")
    if compiler is None:
        pytest.skip("Swift compiler unavailable; macOS CI verifies shared PlayPlan")
    root = Path(__file__).parents[2]
    binary = tmp_path_factory.mktemp("play-swift") / "PlayPlanOracle"
    subprocess.run([compiler, "-parse-as-library", str(root / "Sources/ScoreModels.swift"),
                    str(root / "Sources/StandaloneTransport.swift"),
                    str(Path(__file__).parent / "fixtures/PlayPlanOracle.swift"), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    return binary


def test_swift_plan_matches_python_route_and_timing(swift_play_oracle):
    score = route_score()
    result = subprocess.run([str(swift_play_oracle)], input=json.dumps(score.to_dict()),
                            check=True, capture_output=True, text=True)
    swift = json.loads(result.stdout)
    python = PlayPlan(score).to_dict()
    assert swift["occurrences"] == python["occurrences"]
    assert len(swift["segments"]) == len(python["segments"])
    for a, b in zip(swift["segments"], python["segments"]):
        assert a == pytest.approx(b)
    assert swift["durationSeconds"] == pytest.approx(python["durationSeconds"])


@pytest.mark.parametrize("looped", [False, True])
def test_shared_js_prediction_matches_python_transport_across_tempo_and_repeats(looped):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node runtime unavailable")
    clock = FakeClock()
    document = document_with_score(route_score())
    if looped:
        document = replace(document, loop=LoopRange(1, 6))
    engine = StandaloneTransport(document, clock)
    engine.play()
    initial = engine.snapshot()
    cases = []
    previous_ms = 0
    for elapsed_ms in (0, 399, 400, 700, 900, 1000, 2000, 3700, 7733, 20_000, 70_000):
        clock.advance((elapsed_ms - previous_ms) * 1_000_000)
        previous_ms = elapsed_ms
        cases.append({"targetTime": clock.ns / 1_000_000, "expected": engine.snapshot(include_route=False)})
    script = ("const fs=require('node:fs');const P=require(process.argv[1]);"
              "const d=JSON.parse(fs.readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(d.cases.map(c=>P.position(d.route,d.sample,c.targetTime))));")
    root = Path(__file__).parents[2]
    result = subprocess.run([node, "-e", script, str(root / "Resources/score/PlaybackPlan.js")],
                            input=json.dumps({"route": initial["route"], "sample": initial, "cases": cases}),
                            check=True, text=True, capture_output=True)
    predicted = json.loads(result.stdout)
    for case, actual in zip(cases, predicted):
        expected = case["expected"]
        assert actual["playQuarter"] == pytest.approx(expected["playQuarter"], abs=1e-10)
        assert actual["sourceOffsetQuarter"] == pytest.approx(expected["sourceOffsetQuarter"], abs=1e-10)
        for key in ("sourceMeasureId", "occurrenceId", "bpm", "meterNumerator", "meterDenominator", "playing"):
            assert actual[key] == expected[key]
        if looped:
            assert actual["loopIteration"] == expected["playback"]["loop"]["iteration"]
