"""Manual chart and Logic accessibility-text import, independent of Qt."""

import re
from collections.abc import Iterable

from .models import ChordEvent, PPQ, event_position, validate_meter

_MANUAL_POSITION = re.compile(r"([0-9]+)(?:\.([0-9]+)(?:\.([0-9]+)\.([0-9]+))?)?\Z")
_LOGIC_CHORD = re.compile(
    r"^(.*?) ([0-9]+) bars(?: ([0-9]+) beats)?(?: ([0-9]+) divisions)?(?: ([0-9]+) ticks)?\s*$"
)


def _position_tick(bar: int, beat: int, division: int, tick: int, meter: int) -> int:
    if bar < 1 or not 1 <= beat <= meter or not 1 <= division <= 4 or not 0 <= tick < PPQ:
        raise ValueError("position is outside bar/beat/division/tick bounds")
    # Logic can expose a noncanonical tick component up to 959. Normalize it,
    # provided the resulting position remains inside the specified bar.
    result = (beat - 1) * PPQ + (division - 1) * (PPQ // 4) + tick
    if result >= meter * PPQ:
        raise ValueError("position is outside the bar")
    return result


def _append(events: list[ChordEvent], onsets: set[tuple[int, int]], event: ChordEvent) -> None:
    onset = (event.bar, event.tick)
    if onset in onsets:
        raise ValueError("duplicate chord position")
    onsets.add(onset)
    events.append(event)


def parse_manual(text: str, meter: int = 4) -> tuple[ChordEvent, ...]:
    """Parse ``bar[.beat[.division.tick]] chord``; IDs are source line indices.

    Empty lines are ignored. Every other line must be valid, so a bad edit
    cannot silently delete part of a chart. Returned events are onset sorted.
    """
    validate_meter(meter)
    if not isinstance(text, str):
        raise ValueError("manual chart must be text")
    events: list[ChordEvent] = []
    onsets: set[tuple[int, int]] = set()
    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError("expected a position and chord symbol")
            match = _MANUAL_POSITION.fullmatch(parts[0])
            if match is None:
                raise ValueError("expected bar, bar.beat, or bar.beat.division.tick")
            bar, beat, division, tick = (int(value) if value is not None else default
                                         for value, default in zip(match.groups(), (1, 1, 1, 0)))
            offset = _position_tick(bar, beat, division, tick, meter)
            _append(events, onsets, ChordEvent(index, bar, offset, parts[1].strip()))
        except ValueError as exc:
            raise ValueError(f"Line {index + 1}: {exc}") from exc
    return tuple(sorted(events, key=lambda event: (event.bar, event.tick)))


def format_manual(events: Iterable[ChordEvent]) -> str:
    """Format positions losslessly, using the shortest canonical notation."""
    lines = []
    for event in sorted(events, key=lambda item: (item.bar, item.tick)):
        position = event_position(event)
        bar, beat, division, tick = (position[name] for name in ("bar", "beat", "division", "tick"))
        if division != 1 or tick != 0:
            onset = f"{bar}.{beat}.{division}.{tick}"
        elif beat != 1:
            onset = f"{bar}.{beat}"
        else:
            onset = str(bar)
        lines.append(f"{onset} {event.symbol}")
    return "\n".join(lines)


def _logic_symbol(name: str) -> str:
    """Keep the original LogicReader.symbol(for:) mapping, including its spaces."""
    if name == "No Chord":
        return "N.C."
    split = [part for part in name.split("/", 1) if part]
    if not split:
        return name
    words = [word for word in split[0].split(" ") if word]
    if not words or len(words[0]) != 1 or words[0] not in "abcdefg":
        return name
    index = 1
    root = words[0].upper()
    if len(words) > index and words[index] in ("flat", "sharp"):
        root += "♭" if words[index] == "flat" else "♯"
        index += 1
    quality = " ".join(words[index:])
    suffixes = {
        "": "", "major": "", "major 7": "7", "major major 7": "maj7",
        "major 6": "6", "minor": "m", "minor 7": "m7",
        "minor 7 flat 5": "m7(b5)", "half diminished": "m7(b5)",
        "half diminished 7": "m7(b5)", "minor 6": "m6", "minor major 7": "m(maj7)",
        "major 9": "9", "major major 9": "maj9", "minor 9": "m9", "diminished": "dim",
        "diminished 7": "dim7", "augmented": "aug", "suspended 2": "sus2",
        "suspended 4": "sus4", "augmented 7": "aug7", "5": "5",
    }
    result = root + suffixes.get(quality, " " + quality)
    if len(split) == 2:
        result += "/" + split[1].strip().upper().replace(" SHARP", "♯").replace(" FLAT", "♭")
    return result


def parse_logic_text(text: str, meter: int = 4) -> tuple[ChordEvent, ...]:
    """Import original Logic snapshots, ignoring unrelated accessibility lines.

    Matched chord rows must have valid, unique positions. ``EXPORT|`` prefixes
    are supported just as in tools/KeyAnalysisSnapshot.swift.
    """
    validate_meter(meter)
    if not isinstance(text, str):
        raise ValueError("Logic snapshot must be text")
    events: list[ChordEvent] = []
    onsets: set[tuple[int, int]] = set()
    for index, raw in enumerate(text.splitlines()):
        match = _LOGIC_CHORD.fullmatch(raw.replace("EXPORT|", ""))
        if match is None:
            continue
        try:
            name = match.group(1)
            bar, beat, division, tick = (int(value) if value is not None else default
                                         for value, default in zip(match.groups()[1:], (1, 1, 1, 0)))
            offset = _position_tick(bar, beat, division, tick, meter)
            _append(events, onsets, ChordEvent(index, bar, offset, _logic_symbol(name)))
        except ValueError as exc:
            raise ValueError(f"Line {index + 1}: {exc}") from exc
    return tuple(sorted(events, key=lambda event: (event.bar, event.tick)))
