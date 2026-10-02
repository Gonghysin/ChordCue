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
    def _end_beat(self) -> int:
        return self._document.bars * self._document.meter

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("transport is closed")

    def _loop_bounds(self) -> tuple[int, int] | None:
        loop = self._document.loop
        if loop is None:
            return None
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
        now_ns = self._clock_ns()
        self._document = document
        self._transition(0.0, False, now_ns)

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

    def seek(self, bar: int, beat: float = 1.0) -> None:
        self._require_open()
        if isinstance(bar, bool) or not isinstance(bar, int):
            raise ValueError("bar must be an integer")
        if isinstance(beat, bool) or not isinstance(beat, (int, float)):
            raise ValueError("beat must be a finite number")
        if not 1 <= bar <= self._document.bars + 1:
            raise ValueError("bar is outside the project")
        if not 1 <= beat < self._document.meter + 1:
            raise ValueError("beat is outside the bar")
        position = (bar - 1) * self._document.meter + float(beat) - 1
        if position > self._end_beat:
            raise ValueError("position is beyond the project end")
        position = self._constrain_to_loop(position)
        now_ns = self._clock_ns()
        _, playing, _ = self._state_at(now_ns)
        self._transition(position, playing, now_ns)

    def set_bpm(self, bpm: float) -> None:
        self._require_open()
        document = replace(self._document, bpm=bpm)
        document.validate()
        if document.bpm == self._document.bpm:
            return
        now_ns = self._clock_ns()
        position, playing, _ = self._state_at(now_ns)
        self._document = document
        self._transition(position, playing, now_ns)

    def set_loop(self, loop: LoopRange | None) -> None:
        self._require_open()
        document = replace(self._document, loop=loop)
        document.validate()
        if loop == self._document.loop:
            return
        now_ns = self._clock_ns()
        position, playing, _ = self._state_at(now_ns)
        self._document = document
        if playing:
            position = self._constrain_to_loop(position)
        self._transition(position, playing, now_ns)

    def set_meter(self, meter: int) -> None:
        self._require_open()
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

    def snapshot(self, revision: int = 1) -> dict:
        now_ns = self._clock_ns()
        position, playing, iteration = self._state_at(now_ns)
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

    def close(self) -> None:
        if self._closed:
            return
        self.pause()
        self._closed = True
