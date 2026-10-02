"""Persistent bar-start timing edits without changing a chart's editing identity.

All tempos are quarter-note BPM. Legacy textual chord positions still use
quarters (960 ticks); a 6/8 bar holds three quarters, not six quarter positions.
Source notes and chords retain their exact onsets and durations. Only metadata
already attached to the old bar endpoint follows a changed endpoint.
"""

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import replace
from fractions import Fraction
import hashlib

from .models import ChartDocument, PPQ, TimingChange
from .play_plan import PlayPlan
from .score_models import (
    MAX_MEASURES, QuarterFraction, ScoreChord, ScoreIR, ScoreMeasure, ScoreMeter,
    ScorePart, ScoreSource, ScoreStaff, ScoreTempoChange, ZERO,
)


def _rows(rows: Iterable[TimingChange], bars: int) -> tuple[TimingChange, ...]:
    result: list[TimingChange] = []
    previous = 0
    for row in rows:
        if len(result) >= MAX_MEASURES:
            raise ValueError("变化表最多支持 10000 行")
        if not isinstance(row, TimingChange):
            raise ValueError("timing rows must contain TimingChange objects")
        row.validate()
        if row.bar <= previous or row.bar > bars:
            raise ValueError("变化表的小节须递增、不重复，且位于曲目内")
        result.append(row)
        previous = row.bar
    if not result or result[0].bar != 1:
        raise ValueError("变化表必须从第 1 小节开始")
    return tuple(result)


def timing_rows(document: ChartDocument) -> tuple[TimingChange, ...]:
    """Return compressed effective bar starts, including the required bar 1.

    Source intra-bar/end tempo changes carry into the next source bar. Their
    nonzero positions are retained independently when applying a new table.
    """
    if document.score is None:
        return document.timing_changes or (TimingChange(1, document.bpm, document.meter, 4),)
    changes: dict[str, list[ScoreTempoChange]] = {}
    for change in document.score.tempo_changes:
        changes.setdefault(change.measure_id, []).append(change)
    result: list[TimingChange] = []
    bpm = 120.0
    for bar, measure in enumerate(document.score.measures, 1):
        local = sorted(changes.get(measure.id, ()), key=lambda change: change.offset.as_fraction())
        if local and local[0].offset.numerator == 0:
            bpm = local[0].bpm
        row = TimingChange(bar, bpm, measure.meter.numerator, measure.meter.denominator)
        if not result or (row.bpm, row.numerator, row.denominator) != (
                result[-1].bpm, result[-1].numerator, result[-1].denominator):
            result.append(row)
        if local:
            bpm = local[-1].bpm
    return tuple(result)


def timing_at(document: ChartDocument, bar: int) -> TimingChange:
    """Return the effective start timing at this one-based source bar."""
    if type(bar) is not int or not 1 <= bar <= document.bars:
        raise ValueError("timing bar is outside the project")
    rows = timing_rows(document)
    index = bisect_right(rows, bar, key=lambda row: row.bar) - 1
    return replace(rows[index], bar=bar)


def measure_quarters(document: ChartDocument, bar: int) -> Fraction:
    """Return real source duration (including pickups), or a manual bar capacity."""
    if type(bar) is not int or not 1 <= bar <= document.bars:
        raise ValueError("measure bar is outside the project")
    if document.score is not None:
        return document.score.measures[bar - 1].duration.as_fraction()
    return timing_at(document, bar).meter.quarters


def playback_score(document: ChartDocument) -> ScoreIR | None:
    """Return an authoritative score or an ephemeral manual timing adapter.

    Deterministic adapter IDs retain the legacy event IDs. The adapter is never
    stored in document.score, so manual text editing and legacy key analysis
    remain available. It contains explicit chords and an empty staff, no notes.
    """
    if document.score is not None:
        return document.score
    if not document.timing_changes:
        return None
    document.validate()
    measures: list[ScoreMeasure] = []
    row_index = 0
    for bar in range(1, document.bars + 1):
        if (row_index + 1 < len(document.timing_changes)
                and document.timing_changes[row_index + 1].bar == bar):
            row_index += 1
        meter = document.timing_changes[row_index].meter
        duration = meter.quarters
        measures.append(ScoreMeasure(f"manual.m{bar}", str(bar),
                                     QuarterFraction(duration.numerator, duration.denominator), meter))
    chords: list[ScoreChord] = []
    for event in document.events:
        identifier = f"manual.chord{event.id}"
        if len(identifier) > 128:
            identifier = "manual.chord" + hashlib.sha256(str(event.id).encode("ascii")).hexdigest()
        chords.append(ScoreChord(identifier, f"manual.m{event.bar}",
                                 QuarterFraction(event.tick, PPQ), event.symbol, "manual.staff"))
    return ScoreIR("manual.score", document.name, ScoreSource("manual"), tuple(measures),
                   (ScorePart("manual.part", "和弦谱", staves=(ScoreStaff("manual.staff"),),
                              chords=tuple(chords)),),
                   tempo_changes=tuple(ScoreTempoChange(f"manual.m{row.bar}", ZERO, row.bpm)
                                       for row in document.timing_changes))


def _apply_source(document: ChartDocument, rows: tuple[TimingChange, ...]) -> ChartDocument:
    assert document.score is not None
    score = document.score
    if rows == timing_rows(document):
        return document
    measures: list[ScoreMeasure] = []
    endpoints: dict[str, tuple[QuarterFraction, QuarterFraction]] = {}
    resolved: list[TimingChange] = []
    row_index = 0
    for bar, old in enumerate(score.measures, 1):
        if row_index + 1 < len(rows) and rows[row_index + 1].bar == bar:
            row_index += 1
        row = rows[row_index]
        resolved.append(row)
        new_meter = ScoreMeter(row.numerator, row.denominator)
        if new_meter == old.meter:
            measures.append(old)
            continue
        duration = old.duration.as_fraction()
        if duration == old.meter.quarters:
            duration = new_meter.quarters
        elif duration > new_meter.quarters:
            raise ValueError(f"第 {bar} 小节（{old.number}）原有非标准时长超过新拍号容量")
        new_duration = QuarterFraction(duration.numerator, duration.denominator)
        endpoints[old.id] = old.duration, new_duration

        def endpoint(offset: QuarterFraction) -> QuarterFraction:
            return new_duration if offset == old.duration else offset

        try:
            measures.append(replace(old, meter=new_meter, duration=new_duration,
                                    markers=tuple(replace(marker, offset=endpoint(marker.offset))
                                                  for marker in old.markers),
                                    navigation=tuple(replace(nav, offset=endpoint(nav.offset))
                                                     for nav in old.navigation)))
        except ValueError as error:
            raise ValueError(f"第 {bar} 小节（{old.number}）标记超出新时长：{error}") from error
    by_id = {measure.id: (bar, measure) for bar, measure in enumerate(measures, 1)}

    def within(measure_id: str, offset: QuarterFraction, *, at_end: bool = False,
               duration: QuarterFraction = ZERO, label: str = "位置") -> None:
        bar, measure = by_id[measure_id]
        point, end = offset.as_fraction(), measure.duration.as_fraction()
        if point > end or (point == end and not at_end) or point + duration.as_fraction() > end:
            raise ValueError(f"第 {bar} 小节（{measure.number}）的{label}超出新时长；音符及和弦不会被裁切或移动")

    for part in score.parts:
        for staff in part.staves:
            for event in staff.events:
                within(event.measure_id, event.offset, at_end=event.grace,
                       duration=event.duration, label="音符/休止符")
        for chord in part.chords:
            within(chord.measure_id, chord.offset, label="和弦")

    def endpoint_for(measure_id: str, offset: QuarterFraction) -> QuarterFraction:
        ends = endpoints.get(measure_id)
        return ends[1] if ends is not None and offset == ends[0] else offset

    nonzero_tempos = tuple(replace(change, offset=endpoint_for(change.measure_id, change.offset))
                           for change in score.tempo_changes if change.offset.numerator != 0)
    key_changes = tuple(replace(change, offset=endpoint_for(change.measure_id, change.offset))
                        for change in score.key_changes)
    for changes, label in ((nonzero_tempos, "速度标记"), (key_changes, "调号")):
        onsets: set[tuple[str, Fraction]] = set()
        for change in changes:
            within(change.measure_id, change.offset, at_end=True, label=label)
            onset = change.measure_id, change.offset.as_fraction()
            if onset in onsets:
                bar, measure = by_id[change.measure_id]
                raise ValueError(f"第 {bar} 小节（{measure.number}）的{label}在新小节终点重叠")
            onsets.add(onset)
    tempos = tuple(ScoreTempoChange(measure.id, ZERO, row.bpm)
                   for measure, row in zip(measures, resolved)) + nonzero_tempos
    changed = replace(score, measures=tuple(measures), tempo_changes=tempos, key_changes=key_changes)
    first = rows[0]
    return replace(document, score=changed, bpm=min(300.0, max(20.0, first.bpm)),
                   meter=min(12, max(1, round(float(first.meter.quarters)))))


def apply_timing(document: ChartDocument, rows: Iterable[TimingChange]) -> ChartDocument:
    """Validate a complete candidate and its route before returning it.

    Callers can install the returned document with set_document (stopped at the
    start). Failure leaves the original immutable document and transport intact.
    """
    document.validate()
    validated = _rows(rows, document.bars)
    if document.score is None:
        first = validated[0]
        candidate = replace(document, timing_changes=validated,
                            bpm=min(300.0, max(20.0, first.bpm)),
                            meter=min(12, max(1, round(float(first.meter.quarters)))))
    else:
        candidate = _apply_source(document, validated)
    score = playback_score(candidate)
    assert score is not None
    plan = PlayPlan(score)
    if candidate.loop is not None:
        plan.source_loop_bounds(candidate.loop.start_bar, candidate.loop.end_bar_exclusive)
    return candidate
