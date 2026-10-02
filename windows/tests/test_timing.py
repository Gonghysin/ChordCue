from dataclasses import replace
from fractions import Fraction
import math

import pytest

from chordcue.models import ChartDocument, ChordEvent, LoopRange, TimingChange, document_with_score
from chordcue.play_plan import PlayPlan
from chordcue.score_models import (
    QuarterFraction as Q, ScoreChord, ScoreEvent, ScoreIR, ScoreKeyChange, ScoreMarker,
    ScoreMeasure, ScoreMeter, ScoreNavigation, ScoreNote, ScorePart, ScoreSource,
    ScoreStaff, ScoreTechnique, ScoreTempoChange,
)
from chordcue.timing import apply_timing, measure_quarters, playback_score, timing_at, timing_rows
from chordcue.transport import StandaloneTransport
from test_transport import FakeClock


def source_document(*, duration=Q(4), event_duration=Q(2), chord_offset=Q(1)):
    middle = min(Fraction(1), duration.as_fraction() / 2)
    measure = ScoreMeasure("m1", "A", duration, markers=(ScoreMarker("end", "fine", offset=duration),),
                           navigation=(ScoreNavigation("dc", offset=duration),))
    return document_with_score(ScoreIR("s", "原谱", ScoreSource("musicxml", "original.xml", "a" * 64),
        (measure, ScoreMeasure("m2", "B", Q(4))),
        (ScorePart("p", "吉他", staves=(ScoreStaff("st", kind="tab", tuning=(64,), capo=2, events=(
            ScoreEvent("e1", "m1", Q(0), event_duration, notes=(
                ScoreNote("n1", 64, 1, 0, written_pitch=64,
                          techniques=(ScoreTechnique("vibrato", direction="slight"),)),)),
        )),), chords=(ScoreChord("c1", "m1", chord_offset, "Cmaj7", "st"),)),),
        tempo_changes=(ScoreTempoChange("m1", Q(0), 90),
                       ScoreTempoChange("m1", Q(middle.numerator, middle.denominator), 120),
                       ScoreTempoChange("m1", duration, 140)),
        key_changes=(ScoreKeyChange("m1", duration, 2, "major"),)))


def test_manual_timing_adapter_preserves_text_identity_exact_quarters_and_ids():
    original = ChartDocument(bars=4, events=(ChordEvent(42, 2, 2400, "G7/B"),))
    rows = (TimingChange(1, 60, 4, 4), TimingChange(2, 90, 6, 8), TimingChange(4, 75, 5, 8))
    document = apply_timing(original, rows)
    assert original.timing_changes == ()
    assert document.score is None and document.events is original.events
    assert timing_rows(document) == rows
    assert timing_at(document, 3) == TimingChange(3, 90, 6, 8)
    assert measure_quarters(document, 4) == Fraction(5, 2)
    score = playback_score(document)
    assert score is not None and score.source.format == "manual"
    assert [measure.duration.as_fraction() for measure in score.measures] == [4, 3, 3, Fraction(5, 2)]
    assert score.parts[0].staves[0].events == ()
    assert score.parts[0].chords[0].offset == Q(5, 2)
    assert score.parts[0].chords[0].id == "manual.chord42"
    assert score == playback_score(document)
    assert PlayPlan(score).duration_seconds == pytest.approx(10)
    assert playback_score(original) is None
    assert timing_rows(original) == (TimingChange(1, 120, 4, 4),)


@pytest.mark.parametrize("row", [(True, 60, 4, 4), (0, 60, 4, 4), (1, True, 4, 4),
    (1, math.nan, 4, 4), (1, math.inf, 4, 4), (1, 0.99, 4, 4), (1, 1001, 4, 4),
    (1, 10**1000, 4, 4), (1, 60, True, 4), (1, 60, 65, 4), (1, 60, 4, 3)])
def test_timing_change_strict_finite_validation(row):
    with pytest.raises(ValueError):
        TimingChange(*row)


@pytest.mark.parametrize("rows", [(), (TimingChange(2, 60, 4, 4),),
    (TimingChange(1, 60, 4, 4), TimingChange(1, 80, 3, 4)),
    (TimingChange(1, 60, 4, 4), TimingChange(5, 80, 3, 4)),
    (TimingChange(1, 60, 4, 4), "bad")])
def test_bad_rows_leave_original_chart_unchanged(rows):
    document = ChartDocument(bars=4)
    with pytest.raises(ValueError):
        apply_timing(document, rows)
    assert document == ChartDocument(bars=4)


def test_manual_chord_must_fit_effective_bar_capacity():
    original = ChartDocument(bars=2, events=(ChordEvent(1, 2, 2880, "C"),))
    with pytest.raises(ValueError, match="第 2 小节.*ID 1"):
        apply_timing(original, (TimingChange(1, 60, 4, 4), TimingChange(2, 60, 6, 8)))
    document = ChartDocument(bars=1, meter=4, events=(ChordEvent(1, 1, 7000, "C"),),
                             timing_changes=(TimingChange(1, 60, 8, 4),))
    assert playback_score(document).parts[0].chords[0].offset.as_fraction() == Fraction(175, 24)


def test_manual_table_limits_and_score_exclusion():
    with pytest.raises(ValueError, match="10000"):
        apply_timing(ChartDocument(bars=10001), (TimingChange(1, 60, 4, 4),))
    document = source_document()
    with pytest.raises(ValueError, match="ScoreIR"):
        replace(document, timing_changes=(TimingChange(1, 60, 4, 4),))
    for bar in (0, True, 3):
        with pytest.raises(ValueError):
            timing_at(document, bar)
        with pytest.raises(ValueError):
            measure_quarters(document, bar)


def test_source_changes_preserve_notes_and_move_only_actual_end_metadata():
    original = source_document()
    assert timing_rows(original) == (TimingChange(1, 90, 4, 4), TimingChange(2, 140, 4, 4))
    assert apply_timing(original, timing_rows(original)) is original
    document = apply_timing(original, (TimingChange(1, 60, 6, 8),))
    score = document.score
    assert score.parts is original.score.parts
    assert score.source == original.score.source
    assert score.id == original.score.id and score.format_version == original.score.format_version
    assert score.measures[0].duration == Q(3)
    assert score.measures[0].markers[0].offset == Q(3)
    assert score.measures[0].navigation[0].offset == Q(3)
    assert score.key_changes[0].offset == Q(3)
    assert {(t.measure_id, t.offset, t.bpm) for t in score.tempo_changes} == {
        ("m1", Q(0), 60), ("m2", Q(0), 60), ("m1", Q(1), 120), ("m1", Q(3), 140)}
    assert timing_at(document, 2) == TimingChange(2, 60, 6, 8)
    assert document.timing_changes == ()
    assert original.score.measures[0].duration == Q(4)


@pytest.mark.parametrize("event_duration,chord_offset", [(Q(4), Q(1)), (Q(2), Q(3))])
def test_source_shrink_rejects_notes_or_chords_without_mutation(event_duration, chord_offset):
    original = source_document(event_duration=event_duration, chord_offset=chord_offset)
    before = original.score.to_dict()
    with pytest.raises(ValueError, match="第 1 小节"):
        apply_timing(original, (TimingChange(1, 60, 6, 8),))
    assert original.score.to_dict() == before


def test_pickup_keeps_actual_duration_and_nonstandard_no_op_is_allowed():
    pickup = source_document(duration=Q(1, 2), event_duration=Q(1, 4), chord_offset=Q(0))
    # The original mid-bar tempo must be valid for the pickup.
    pickup = replace(pickup, score=replace(pickup.score, tempo_changes=(
        ScoreTempoChange("m1", Q(0), 90), ScoreTempoChange("m1", Q(1, 2), 140))))
    edited = apply_timing(pickup, (TimingChange(1, 90, 6, 8), TimingChange(2, 140, 4, 4)))
    assert measure_quarters(edited, 1) == Fraction(1, 2)
    assert edited.score.parts == pickup.score.parts
    irregular = source_document(duration=Q(5))
    assert apply_timing(irregular, timing_rows(irregular)) is irregular
    assert apply_timing(irregular, (TimingChange(1, 60, 4, 4),)).score.measures[0].duration == Q(5)
    with pytest.raises(ValueError, match="非标准"):
        apply_timing(irregular, (TimingChange(1, 60, 6, 8),))


def test_source_endpoint_collision_rejected_with_source_bar():
    original = source_document()
    original = replace(original, score=replace(original.score, tempo_changes=original.score.tempo_changes + (
        ScoreTempoChange("m1", Q(3), 130),)))
    with pytest.raises(ValueError, match="第 1 小节.*重叠"):
        apply_timing(original, (TimingChange(1, 60, 6, 8),))


def test_mapped_transport_switches_meter_and_bpm_at_exact_boundaries_and_ends():
    clock = FakeClock()
    document = apply_timing(ChartDocument(bars=3), (
        TimingChange(1, 60, 4, 4), TimingChange(2, 120, 6, 8), TimingChange(3, 90, 5, 8)))
    transport = StandaloneTransport(document, clock)
    assert transport.plan is not None and transport.document.score is None
    transport.play()
    epoch = transport.snapshot()["discontinuity"]
    clock.advance(4_400_000_000)
    sample = transport.snapshot(include_route=False)
    assert (sample["bar"], sample["playQuarter"], sample["bpm"], sample["meter"]) == (2, 4, 120, "6/8")
    assert sample["sourceMeasureId"] == "manual.m2" and "route" not in sample
    clock.advance(1_500_000_000)
    assert transport.snapshot()["meter"] == "5/8"
    assert transport.snapshot()["bpm"] == 90
    assert transport.snapshot()["discontinuity"] == epoch
    clock.advance(2_000_000_000)
    assert not transport.snapshot()["playing"] and transport.snapshot()["bar"] == 4


def test_mapped_transport_seek_loop_and_setters_are_atomic():
    clock = FakeClock()
    document = apply_timing(ChartDocument(bars=3), (
        TimingChange(1, 60, 4, 4), TimingChange(2, 120, 6, 8)))
    transport = StandaloneTransport(document, clock)
    transport.seek(2, 3.5)
    assert transport.snapshot()["playQuarter"] == 6.5
    before = transport.snapshot()
    for operation in (lambda: transport.seek(2, 4), lambda: transport.set_bpm(100),
                      lambda: transport.set_meter(3)):
        with pytest.raises(ValueError):
            operation()
        assert transport.snapshot() == before
    transport.set_loop(LoopRange(2, 4))
    transport.play()
    clock.advance(3_400_000_000)
    sample = transport.snapshot()
    assert sample["playQuarter"] == pytest.approx(6.5)
    assert sample["playback"]["loop"]["iteration"] == 1
    transport.set_document(apply_timing(document, (TimingChange(1, 80, 3, 4),)))
    assert transport.snapshot()["playQuarter"] == 0 and not transport.snapshot()["playing"]


def test_extreme_bpm_is_authoritative_and_legacy_fallback_is_bounded():
    for bpm, fallback in ((1, 20), (1000, 300)):
        document = apply_timing(ChartDocument(bars=1), (TimingChange(1, bpm, 64, 1),))
        assert document.bpm == fallback and document.meter == 12
        assert StandaloneTransport(document).snapshot()["bpm"] == bpm


def test_candidate_route_checks_existing_source_loop_before_returning_document():
    original = replace(source_document(), loop=LoopRange(2, 3))
    with pytest.raises(ValueError):
        apply_timing(original, (TimingChange(1, 60, 6, 8),))
    assert original.score.measures[0].meter == ScoreMeter(4, 4)


def test_unmapped_legacy_large_project_still_has_timing_lookup():
    document = ChartDocument(bars=100_000)
    assert timing_at(document, 100_000) == TimingChange(100_000, 120, 4, 4)
    assert measure_quarters(document, 100_000) == 4


@pytest.mark.parametrize("cycles", [1, 3, 100, 1000])
@pytest.mark.parametrize("delta_ns", [-1, 0, 1])
def test_mixed_tempo_long_loops_have_one_consistent_phase_at_boundary(cycles, delta_ns):
    clock = FakeClock()
    document = apply_timing(ChartDocument(bars=3, loop=LoopRange(1, 4)), (
        TimingChange(1, 120, 4, 4), TimingChange(2, 90, 6, 8), TimingChange(3, 150, 7, 8)))
    engine = StandaloneTransport(document, clock)
    assert engine.plan.duration_seconds == pytest.approx(5.4)
    engine.play()
    epoch = engine.snapshot()["discontinuity"]
    clock.advance(400_000_000 + cycles * 5_400_000_000 + delta_ns)
    sample = engine.snapshot(include_route=False)
    assert sample["playback"]["loop"]["iteration"] == cycles - (delta_ns < 0)
    assert sample["discontinuity"] == epoch
    if delta_ns < 0:
        assert sample["bar"] == 3 and 10.49999999 < sample["playQuarter"] < 10.5
    elif delta_ns == 0:
        assert sample["bar"] == 1 and sample["playQuarter"] == 0
    else:
        assert sample["bar"] == 1 and 0 < sample["playQuarter"] < 1e-8
