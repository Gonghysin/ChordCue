"""Bounded playback occurrences and tempo integration over an immutable source score."""

from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import math
import hashlib
import json
from typing import Any

from .score_models import ScoreIR, ScoreTempoChange, ScoreWarning

MAX_OCCURRENCES = 100_000
MAX_ROUTE_QUARTERS = 1_000_000
MAX_SEGMENTS = 200_000


@dataclass(frozen=True)
class PlayOccurrence:
    id: str
    source_measure_id: str
    source_index: int
    source_number: str
    start_quarter: Fraction
    end_quarter: Fraction
    meter_numerator: int
    meter_denominator: int


@dataclass(frozen=True)
class TempoSegment:
    start_quarter: Fraction
    end_quarter: Fraction
    start_seconds: float
    end_seconds: float
    bpm: float
    occurrence_id: str
    source_measure_id: str
    source_offset_quarter: Fraction


class PlayPlan:
    """A source-preserving route; source IDs are never rewritten for repeats.

    Repeats nest up to eight levels and endings select their enclosing repeat
    pass. DC/DS are followed once; after a jump, written repeats are omitted and
    final endings selected. Mid-measure jumps and targets remain explicit
    warnings rather than silently shortening the original music.
    """

    def __init__(self, score: ScoreIR, tempo_scale: float = 1.0) -> None:
        score.validate()
        if isinstance(tempo_scale, bool) or not isinstance(tempo_scale, (int, float)):
            raise ValueError("tempo scale must be finite and in 0.02..50")
        if not math.isfinite(tempo_scale) or not 0.02 <= tempo_scale <= 50:
            raise ValueError("tempo scale must be finite and in 0.02..50")
        self.score = score
        self.tempo_scale = tempo_scale
        warnings = list(score.warnings)
        warning_keys = {(item.code, item.measure_id) for item in warnings}

        def warn(code: str, message: str, index: int) -> None:
            key = (code, score.measures[index].id)
            if key not in warning_keys:
                if len(warnings) >= 10_000:
                    raise ValueError("route warning complexity exceeds limit")
                warnings.append(ScoreWarning(code, message, measure_id=key[1]))
                warning_keys.add(key)

        # Pair written repeat boundaries, including the implicit score start.
        repeats: dict[int, tuple[int, int, int]] = {}
        stack: list[int] = []
        for index, measure in enumerate(score.measures):
            if measure.repeat_start:
                stack.append(index)
                if len(stack) > 8:
                    raise ValueError("repeat nesting exceeds eight levels")
            if measure.repeat_end is not None:
                start = stack.pop() if stack else 0
                if start in repeats:
                    warn("repeat-ambiguous", "Multiple repeat ends share a start; later repeat omitted", index)
                    continue
                extended = index
                while extended + 1 < len(score.measures) and score.measures[extended + 1].ending_numbers:
                    extended += 1
                repeats[start] = (index, extended, measure.repeat_end)
        for start in stack:
            warn("repeat-unclosed", "Repeat start has no matching end; playing it once", start)
        final_pass: dict[int, int] = {}
        expanded: list[int] = []

        def emit_range(start: int, end: int, active_pass: int | None = None,
                       ignore_start: int | None = None, depth: int = 0) -> None:
            if depth > 8:
                raise ValueError("repeat nesting exceeds eight levels")
            index = start
            while index <= end:
                measure = score.measures[index]
                if measure.ending_numbers and active_pass is not None and active_pass not in measure.ending_numbers:
                    index += 1
                    continue
                if index in repeats and index != ignore_start:
                    _, extended, passes = repeats[index]
                    if extended > end:
                        warn("repeat-overlap", "Overlapping repeat/ending span is played once", index)
                    else:
                        for item in range(index, extended + 1):
                            final_pass[item] = passes
                        for traversal in range(1, passes + 1):
                            emit_range(index, extended, traversal, index, depth + 1)
                        index = extended + 1
                        continue
                if measure.ending_numbers and active_pass is None:
                    warn("ending-unscoped", "Ending has no enclosing repeat; playing source once", index)
                expanded.append(index)
                if len(expanded) > MAX_OCCURRENCES:
                    raise ValueError("playback route exceeds occurrence limit")
                index += 1

        emit_range(0, len(score.measures) - 1)
        marker_locations = {marker.id: (index, marker.offset.as_fraction())
                            for index, measure in enumerate(score.measures) for marker in measure.markers}
        route: list[int] = []
        queue = expanded
        pointer = 0
        jumped = False
        executed: set[tuple[int, str]] = set()
        while pointer < len(queue):
            index = queue[pointer]
            measure = score.measures[index]
            route.append(index)
            if len(route) > MAX_OCCURRENCES:
                raise ValueError("navigation exceeds occurrence limit")
            pointer += 1
            if jumped and any(marker.kind == "fine" for marker in measure.markers):
                break
            for nav in measure.navigation:
                if nav.offset.as_fraction() != measure.duration.as_fraction():
                    warn("navigation-mid-measure", "Mid-measure navigation is not executed; verify route preview", index)
                    continue
                if nav.kind == "fine":
                    if jumped:
                        pointer = len(queue)
                    continue
                if nav.kind == "toCoda" and not jumped:
                    continue
                key = (index, nav.kind)
                if key in executed:
                    continue
                target = 0 if nav.kind == "dc" else None
                if nav.target_marker_id is not None:
                    target, offset = marker_locations[nav.target_marker_id]
                    if offset:
                        warn("navigation-mid-target", "Mid-measure navigation target is not executed", index)
                        continue
                if target is None:
                    warn("navigation-unresolved", "Navigation target is unresolved; playing source continuation", index)
                    continue
                executed.add(key)
                jumped = True
                queue = [i for i in range(target, len(score.measures))
                         if not score.measures[i].ending_numbers
                         or final_pass.get(i, max(score.measures[i].ending_numbers)) in score.measures[i].ending_numbers]
                pointer = 0
                break

        if not route:
            raise ValueError("score produces an empty playback route")
        # Effective tempo is based on source score order, then restored on every
        # occurrence, so a repeat/jump to an earlier bar restores its own tempo.
        changes: dict[str, list[ScoreTempoChange]] = {}
        for change in score.tempo_changes:
            changes.setdefault(change.measure_id, []).append(change)
        local_tempos: dict[int, list[tuple[Fraction, Fraction, float]]] = {}
        bpm = 120.0
        for index, measure in enumerate(score.measures):
            points = sorted(changes.get(measure.id, []), key=lambda item: item.offset.as_fraction())
            local_segments: list[tuple[Fraction, Fraction, float]] = []
            offset = Fraction(0)
            for change in points:
                point = change.offset.as_fraction()
                if point > offset:
                    local_segments.append((offset, point, bpm * tempo_scale))
                bpm = change.bpm
                offset = point
            if offset < measure.duration.as_fraction():
                local_segments.append((offset, measure.duration.as_fraction(), bpm * tempo_scale))
            local_tempos[index] = local_segments
        occurrences: list[PlayOccurrence] = []
        segments: list[TempoSegment] = []
        quarter = Fraction(0)
        seconds = 0.0
        visits: dict[str, int] = {}
        for index in route:
            measure = score.measures[index]
            visits[measure.id] = visits.get(measure.id, 0) + 1
            identifier = f"{measure.id}@{visits[measure.id]}"
            end = quarter + measure.duration.as_fraction()
            if end > MAX_ROUTE_QUARTERS:
                raise ValueError("playback route exceeds quarter-note limit")
            if end.denominator > 1_000_000:
                raise ValueError("route rational complexity exceeds denominator limit")
            occurrences.append(PlayOccurrence(identifier, measure.id, index + 1, measure.number,
                                              quarter, end, measure.meter.numerator, measure.meter.denominator))
            for start_offset, end_offset, tempo in local_tempos[index]:
                if ((quarter + start_offset).denominator > 1_000_000
                        or (quarter + end_offset).denominator > 1_000_000):
                    raise ValueError("route rational complexity exceeds denominator limit")
                duration_seconds = float(end_offset - start_offset) * 60 / tempo
                segments.append(TempoSegment(quarter + start_offset, quarter + end_offset, seconds,
                                             seconds + duration_seconds, tempo, identifier, measure.id, start_offset))
                if len(segments) > MAX_SEGMENTS:
                    raise ValueError("tempo segment limit exceeded")
                seconds += duration_seconds
            quarter = end
        self.occurrences = tuple(occurrences)
        self.segments = tuple(segments)
        self.end_quarter = quarter
        self.duration_seconds = seconds
        self.warnings = tuple(warnings)
        self._quarter_starts = tuple(float(item.start_quarter) for item in self.segments)
        self._second_starts = tuple(item.start_seconds for item in self.segments)
        self._occurrence_starts = tuple(float(item.start_quarter) for item in self.occurrences)
        self._wire = self._to_wire()
        encoded = json.dumps(self._wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > 32 * 1024 * 1024:
            raise ValueError("route JSON exceeds 32 MiB")
        self.route_id = hashlib.sha256(encoded).hexdigest()[:24]

    def time_at(self, quarter: float) -> float:
        if quarter >= float(self.end_quarter):
            return self.duration_seconds
        segment = self.segments[max(0, bisect_right(self._quarter_starts, quarter) - 1)]
        return segment.start_seconds + (quarter - float(segment.start_quarter)) * 60 / segment.bpm

    def quarter_at(self, seconds: float) -> float:
        if seconds >= self.duration_seconds:
            return float(self.end_quarter)
        segment = self.segments[max(0, bisect_right(self._second_starts, seconds) - 1)]
        return float(segment.start_quarter) + (seconds - segment.start_seconds) * segment.bpm / 60

    def occurrence_at(self, quarter: float) -> PlayOccurrence:
        return self.occurrences[max(0, bisect_right(self._occurrence_starts, quarter) - 1)]

    def bpm_at(self, quarter: float) -> float:
        return self.segments[max(0, bisect_right(self._quarter_starts, quarter) - 1)].bpm

    def source_position(self, source_index: int, offset: float = 0.0, occurrence: int = 1) -> float:
        if type(source_index) is not int or type(occurrence) is not int or occurrence < 1:
            raise ValueError("source index and occurrence must be positive integers")
        try:
            valid = not isinstance(offset, bool) and isinstance(offset, (int, float)) and math.isfinite(offset)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("source offset must be finite")
        candidates = [item for item in self.occurrences if item.source_index == source_index]
        if occurrence > len(candidates):
            raise ValueError("source measure occurrence is outside playback route")
        item = candidates[occurrence - 1]
        if not 0 <= offset < float(item.end_quarter - item.start_quarter):
            raise ValueError("source offset is outside the measure")
        return float(item.start_quarter) + offset

    def source_loop_bounds(self, start: int, end_exclusive: int) -> tuple[float, float]:
        # First contiguous route passage through the selected source interval.
        first = next((i for i, item in enumerate(self.occurrences)
                      if start <= item.source_index < end_exclusive), None)
        if first is None:
            raise ValueError("loop contains no performed source measures")
        end = first
        while end + 1 < len(self.occurrences) and start <= self.occurrences[end + 1].source_index < end_exclusive:
            end += 1
        return float(self.occurrences[first].start_quarter), float(self.occurrences[end].end_quarter)

    def to_dict(self, *, copy: bool = True) -> dict[str, Any]:
        """Return a safe JSON copy; copy=False is an internal read-only cache view."""
        return deepcopy(self._wire) if copy else self._wire

    def _to_wire(self) -> dict[str, Any]:
        return {
            "version": 1, "endQuarter": float(self.end_quarter), "durationSeconds": self.duration_seconds,
            "occurrences": [{"id": item.id, "sourceMeasureId": item.source_measure_id,
                "sourceIndex": item.source_index, "sourceNumber": item.source_number,
                "startQuarter": float(item.start_quarter), "endQuarter": float(item.end_quarter),
                "meter": {"numerator": item.meter_numerator, "denominator": item.meter_denominator}}
                for item in self.occurrences],
            "segments": [{"startQuarter": float(item.start_quarter), "endQuarter": float(item.end_quarter),
                "startSeconds": item.start_seconds, "endSeconds": item.end_seconds, "bpm": item.bpm,
                "occurrenceId": item.occurrence_id, "sourceMeasureId": item.source_measure_id,
                "sourceOffsetQuarter": float(item.source_offset_quarter)} for item in self.segments],
            "warnings": [{"code": item.code, "message": item.message, "severity": item.severity,
                "measureId": item.measure_id, "partId": item.part_id} for item in self.warnings],
        }
