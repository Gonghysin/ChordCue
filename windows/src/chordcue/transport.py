"""Standalone musical transport, driven only by a monotonic clock.

The clock anchor retains fractional quarter-note beats. Reading a sample never
feeds its tick-quantized wire position back into the clock, so polling frequency
cannot affect tempo or accumulate loop drift.
"""

from __future__ import annotations

from dataclasses import replace
import math
import time
from typing import Callable, Protocol, runtime_checkable

from .models import ChartDocument, LoopRange, PPQ
from .play_plan import PlayPlan
from .timing import playback_score


PREPARATION_NS = 400_000_000


@runtime_checkable
class HostAdapter(Protocol):
    """Read-only capabilities shared by standalone and future external hosts."""

    @property
    def document(self) -> ChartDocument: ...

    def snapshot(self, revision: int = 1) -> dict: ...


@runtime_checkable
class TransportController(HostAdapter, Protocol):
    """Optional controls; consumers reading a host need not have these."""

    def set_document(self, document: ChartDocument) -> None: ...

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def seek(self, bar: int, beat: float = 1.0) -> None: ...

    def set_bpm(self, bpm: float) -> None: ...

    def set_loop(self, loop: LoopRange | None) -> None: ...

    def set_meter(self, meter: int) -> None: ...

    def close(self) -> None: ...


class StandaloneTransport:
    """A timer-free transport intended to be controlled by one owner thread.

    Play and resume announce a start 400 ms in the future. A manual edit during
    preparation keeps that start; an edit during playback takes effect now.
    Manual transitions reset loop iteration and advance the discontinuity epoch.
    Natural loop boundaries and the project end do neither.
    """

    def __init__(
        self,
        document: ChartDocument,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        document.validate()
        score = playback_score(document)
        self._plan = PlayPlan(score) if score is not None else None
        self._score_loop_bounds = (self._plan.source_loop_bounds(document.loop.start_bar, document.loop.end_bar_exclusive)
                                   if self._plan is not None and document.loop is not None else None)
        self._tempo_scale = 1.0
        self._document = document
        self._clock_ns = clock_ns
        self._position = 0.0
        self._playing = False
        self._anchor_ns: int | None = None
        self._discontinuity = 0
        self._closed = False

    @property
    def document(self) -> ChartDocument:
        return self._document

    @property
    def plan(self) -> PlayPlan | None:
        return self._plan

    @property
    def _end_beat(self) -> float:
        if self._plan is not None:
            return float(self._plan.end_quarter)
        return self._document.bars * self._document.meter

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("transport is closed")

    def _loop_bounds(self) -> tuple[float, float] | None:
        loop = self._document.loop
        if loop is None:
            return None
        if self._plan is not None:
            return self._score_loop_bounds
        meter = self._document.meter
        return (loop.start_bar - 1) * meter, (loop.end_bar_exclusive - 1) * meter

    def _constrain_to_loop(self, position: float) -> float:
        bounds = self._loop_bounds()
        if bounds is not None and not bounds[0] <= position < bounds[1]:
            return float(bounds[0])
        return position

    def _state_at(self, now_ns: int) -> tuple[float, bool, int]:
        """Derive phase from one anchor; do not repeatedly integrate samples."""
        if not self._playing or self._anchor_ns is None:
            return self._position, False, 0
        elapsed_ns = max(0, now_ns - self._anchor_ns)
        if self._plan is not None:
            seconds = self._plan.time_at(self._position) + elapsed_ns / 1_000_000_000
            bounds = self._loop_bounds()
            if bounds is not None:
                start, end = map(self._plan.time_at, bounds)
                length = end - start
                elapsed = seconds - start
                ratio = elapsed / length
                nearest = round(ratio)
                # Division and modulo can disagree at an exact double boundary
                # (16.2 / 5.4 is slightly below 3). Derive iteration and phase
                # together, correcting only representation noise. The half-ns
                # cap keeps a real clock reading 1 ns before a boundary before it.
                tolerance = min(0.5e-9 / length, 2 * math.ulp(1.0) * max(1, abs(ratio)))
                if abs(ratio - nearest) <= tolerance:
                    iteration, phase = nearest, 0.0
                else:
                    iteration = math.floor(ratio)
                    phase = elapsed - iteration * length
                return self._plan.quarter_at(start + phase), True, iteration
            if seconds >= self._plan.duration_seconds:
                return self._end_beat, False, 0
            return self._plan.quarter_at(seconds), True, 0
        position = self._position + elapsed_ns * self._document.bpm / 60_000_000_000
        bounds = self._loop_bounds()
        if bounds is not None:
            start, end = bounds
            length = end - start
            iteration = math.floor((position - start) / length)
            return start + (position - start) % length, True, iteration
        if position >= self._end_beat:
            return float(self._end_beat), False, 0
        return position, True, 0

    def _transition(
        self,
        position: float,
        playing: bool,
        now_ns: int,
        *,
        prepare: bool = False,
    ) -> None:
        previous_start = self._anchor_ns
        self._position = position
        self._playing = playing
        if not playing:
            self._anchor_ns = None
        elif prepare:
            self._anchor_ns = now_ns + PREPARATION_NS
        elif previous_start is not None and previous_start > now_ns:
            self._anchor_ns = previous_start
        else:
            self._anchor_ns = now_ns
        self._discontinuity += 1

    def set_document(self, document: ChartDocument) -> None:
        self._require_open()
        document.validate()
        score = playback_score(document)
        plan = PlayPlan(score) if score is not None else None
        if plan is not None and document.loop is not None:
            plan.source_loop_bounds(document.loop.start_bar, document.loop.end_bar_exclusive)
        now_ns = self._clock_ns()
        self._document = document
        self._plan = plan
        self._score_loop_bounds = (plan.source_loop_bounds(document.loop.start_bar, document.loop.end_bar_exclusive)
                                   if plan is not None and document.loop is not None else None)
        self._tempo_scale = 1.0
        self._transition(0.0, False, now_ns)

    def select_part(self, part_id: str) -> None:
        """Changing only the displayed part preserves playback phase and epoch."""
        self._require_open()
        if self._document.score is None:
            raise ValueError("part selection requires a score")
        self._document = replace(self._document, selected_part_id=part_id)

    def play(self) -> None:
        self._require_open()
        now_ns = self._clock_ns()
        position, playing, _ = self._state_at(now_ns)
        if playing:
            return
        if position >= self._end_beat:
            position = 0.0
        position = self._constrain_to_loop(position)
        self._transition(position, True, now_ns, prepare=True)

    def pause(self) -> None:
        self._require_open()
        now_ns = self._clock_ns()
        position, _, _ = self._state_at(now_ns)
        if self._playing:
            self._transition(position, False, now_ns)

    def stop(self) -> None:
        self._require_open()
        self._transition(0.0, False, self._clock_ns())

    def seek(self, bar: int, beat: float = 1.0, occurrence: int = 1) -> None:
        self._require_open()
        if isinstance(bar, bool) or not isinstance(bar, int):
            raise ValueError("bar must be an integer")
        if isinstance(beat, bool) or not isinstance(beat, (int, float)):
            raise ValueError("beat must be a finite number")
        if not 1 <= bar <= self._document.bars + 1:
            raise ValueError("bar is outside the project")
        if self._plan is not None:
            try:
                offset = float(beat) - 1
            except OverflowError as error:
                raise ValueError("beat must be finite") from error
            if not math.isfinite(offset):
                raise ValueError("beat must be finite")
            if bar == self._document.bars + 1 and beat == 1:
                self.seek_quarter(self._end_beat)
            else:
                self.seek_quarter(self._plan.source_position(bar, offset, occurrence))
            return
        if not 1 <= beat < self._document.meter + 1:
            raise ValueError("beat is outside the bar")
        position = (bar - 1) * self._document.meter + float(beat) - 1
        if position > self._end_beat:
            raise ValueError("position is beyond the project end")
        position = self._constrain_to_loop(position)
        now_ns = self._clock_ns()
        _, playing, _ = self._state_at(now_ns)
        self._transition(position, playing, now_ns)

    def seek_quarter(self, quarter: float) -> None:
        self._require_open()
        try:
            valid = (not isinstance(quarter, bool) and isinstance(quarter, (int, float))
                     and math.isfinite(quarter) and 0 <= quarter <= self._end_beat)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("route quarter must be finite and within playback range")
        position = self._constrain_to_loop(float(quarter))
        now_ns = self._clock_ns()
        _, playing, _ = self._state_at(now_ns)
        self._transition(position, playing, now_ns)

    def set_bpm(self, bpm: float) -> None:
        self._require_open()
        if self._document.timing_changes:
            raise ValueError("有变化表的手工谱请在变化表中修改 BPM")
        document = replace(self._document, bpm=bpm)
        document.validate()
        if document.bpm == self._document.bpm:
            return
        now_ns = self._clock_ns()
        position, playing, _ = self._state_at(now_ns)
        plan = self._plan
        scale = self._tempo_scale
        if document.score is not None:
            scale *= document.bpm / self._document.bpm
            plan = PlayPlan(document.score, scale)
        self._document = document
        self._plan = plan
        self._tempo_scale = scale
        self._transition(position, playing, now_ns)

    def set_loop(self, loop: LoopRange | None) -> None:
        self._require_open()
        document = replace(self._document, loop=loop)
        document.validate()
        if self._plan is not None and loop is not None:
            self._plan.source_loop_bounds(loop.start_bar, loop.end_bar_exclusive)
        if loop == self._document.loop:
            return
        now_ns = self._clock_ns()
        position, playing, _ = self._state_at(now_ns)
        self._document = document
        self._score_loop_bounds = (self._plan.source_loop_bounds(loop.start_bar, loop.end_bar_exclusive)
                                   if self._plan is not None and loop is not None else None)
        if playing:
            position = self._constrain_to_loop(position)
        self._transition(position, playing, now_ns)

    def set_meter(self, meter: int) -> None:
        self._require_open()
        if self._plan is not None:
            if self._document.timing_changes:
                raise ValueError("有变化表的手工谱请在变化表中修改拍号")
            raise ValueError("imported score meter is defined by its source map")
        document = replace(self._document, meter=meter)
        document.validate()
        now_ns = self._clock_ns()
        position, playing, _ = self._state_at(now_ns)
        if playing:
            raise ValueError("meter can only change while stopped")
        if meter == self._document.meter:
            return
        bar_index, within_bar = divmod(position, self._document.meter)
        if within_bar >= meter:
            raise ValueError("new meter would invalidate the playhead position")
        position = bar_index * meter + within_bar
        self._document = document
        self._transition(position, False, now_ns)

    def snapshot(self, revision: int = 1, *, include_route: bool = True) -> dict:
        now_ns = self._clock_ns()
        position, playing, iteration = self._state_at(now_ns)
        if self._plan is not None:
            return self._score_snapshot(now_ns, position, playing, iteration, revision, include_route)
        # Floor only at this boundary: the sample must never report a future
        # beat or the exclusive loop endpoint before the actual clock gets there.
        total_ticks = math.floor(position * PPQ)
        bar_index, bar_ticks = divmod(total_ticks, self._document.meter * PPQ)
        beat_index, beat_ticks = divmod(bar_ticks, PPQ)
        division_index, tick = divmod(beat_ticks, PPQ // 4)
        bounds = self._loop_bounds()
        playback: dict = {"endBeat": self._end_beat, "loop": None}
        if playing and self._anchor_ns is not None:
            playback["startTime"] = self._anchor_ns / 1_000_000
        if bounds is not None:
            playback["loop"] = {
                "startBeat": bounds[0],
                "endBeat": bounds[1],
                "iteration": iteration,
            }
        return {
            "revision": revision,
            "sampleTime": now_ns / 1_000_000,
            "readMs": 0.0,
            "valid": not self._closed,
            "precise": not self._closed,
            "rate": self._document.bpm / 60,
            "bar": bar_index + 1,
            "beat": beat_index + 1,
            "division": division_index + 1,
            "tick": tick,
            "bpm": self._document.bpm,
            "meter": f"{self._document.meter}/4",
            "playing": playing,
            "discontinuity": self._discontinuity,
            "playback": playback,
        }

    def _score_snapshot(self, now_ns: int, position: float, playing: bool, iteration: int,
                        revision: int, include_route: bool) -> dict:
        assert self._plan is not None
        occurrence = self._plan.occurrence_at(position)
        offset = position - float(occurrence.start_quarter)
        total_ticks = math.floor(max(0, offset) * PPQ)
        beat_index, within = divmod(total_ticks, PPQ)
        division, tick = divmod(within, PPQ // 4)
        bpm = self._plan.bpm_at(position)
        bounds = self._loop_bounds()
        playback: dict = {"endBeat": self._end_beat, "loop": None}
        if playing and self._anchor_ns is not None:
            playback["startTime"] = self._anchor_ns / 1_000_000
        if bounds is not None:
            playback["loop"] = {"startBeat": bounds[0], "endBeat": bounds[1], "iteration": iteration}
        at_end = position >= self._end_beat
        sample = {"revision": revision, "sampleTime": now_ns / 1_000_000, "readMs": 0.0,
            "valid": not self._closed, "precise": not self._closed, "rate": bpm / 60,
            "bar": self._document.bars + 1 if at_end else occurrence.source_index,
            "beat": 1 if at_end else beat_index + 1, "division": 1 if at_end else division + 1,
            "tick": 0 if at_end else tick, "bpm": bpm,
            "meter": f"{occurrence.meter_numerator}/{occurrence.meter_denominator}",
            "meterNumerator": occurrence.meter_numerator, "meterDenominator": occurrence.meter_denominator,
            "playing": playing, "preparing": playing and self._anchor_ns is not None and now_ns < self._anchor_ns,
            "discontinuity": self._discontinuity, "playback": playback, "playQuarter": position,
            "positionQuarter": position, "sourceMeasureId": occurrence.source_measure_id,
            "sourceOffsetQuarter": offset, "occurrenceId": occurrence.id,
            "selectedPartId": self._document.selected_part_id, "routeId": self._plan.route_id}
        if include_route:
            sample["route"] = self._plan.to_dict(copy=False)
        return sample

    def close(self) -> None:
        if self._closed:
            return
        self.pause()
        self._closed = True
