"""Immutable, validated project data shared by every Windows component.

Positions use 960 ticks per quarter note.  Only serialization to the existing
browser protocol uses the older beat/division/tick representation.
"""

from dataclasses import dataclass
from math import isfinite

PPQ = 960
SUPPORTED_METERS = tuple(range(1, 13))


def _integer(value: object, name: str, minimum: int, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is out of range")


def validate_meter(meter: object) -> None:
    _integer(meter, "meter", 1, 12)


def validate_bpm(bpm: object) -> None:
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)):
        raise ValueError("bpm must be a finite number from 20 to 300")
    try:
        valid = isfinite(bpm) and 20 <= bpm <= 300
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("bpm must be a finite number from 20 to 300")


@dataclass(frozen=True)
class MusicalKey:
    root: int
    is_minor: bool = False

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _integer(self.root, "key root", 0, 11)
        if type(self.is_minor) is not bool:
            raise ValueError("key minor must be a boolean")

    @property
    def major_family_root(self) -> int:
        return (self.root + (3 if self.is_minor else 0)) % 12

    @property
    def label(self) -> str:
        from .theory import note_name

        main = f"1={note_name(self.major_family_root)}"
        return main + f" · 6={note_name(self.root)}（小调）" if self.is_minor else main

    @property
    def selection_label(self) -> str:
        from .theory import note_name

        return note_name(self.root) + (" 小调" if self.is_minor else " 大调")


@dataclass(frozen=True)
class KeySection:
    first_bar: int
    key: MusicalKey

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _integer(self.first_bar, "section bar", 1)
        if not isinstance(self.key, MusicalKey):
            raise ValueError("section key must be a MusicalKey")
        self.key.validate()


@dataclass(frozen=True)
class ChordEvent:
    id: int
    bar: int
    tick: int
    symbol: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _integer(self.id, "event id", 0)
        _integer(self.bar, "event bar", 1)
        _integer(self.tick, "event tick", 0)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("event symbol must be nonempty text")
        if any(ord(c) < 32 or c in "\x7f\u0085\u2028\u2029" for c in self.symbol):
            raise ValueError("event symbol must be a single line without control characters")


@dataclass(frozen=True)
class LoopRange:
    start_bar: int
    end_bar_exclusive: int

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _integer(self.start_bar, "loop start", 1)
        _integer(self.end_bar_exclusive, "loop end", 2)
        if self.end_bar_exclusive <= self.start_bar:
            raise ValueError("loop end must follow loop start")


@dataclass(frozen=True)
class ChartDocument:
    name: str = "未命名"
    bars: int = 16
    bpm: float = 120.0
    meter: int = 4
    events: tuple[ChordEvent, ...] = ()
    forced_key: MusicalKey | None = None
    manual_sections: tuple[KeySection, ...] = ()
    detect_changes: bool = True
    original_key: MusicalKey | None = None
    loop: LoopRange | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("project name must be nonempty text")
        if any(ord(c) < 32 for c in self.name):
            raise ValueError("project name cannot contain control characters")
        _integer(self.bars, "bars", 1)
        if self.bars > 100_000:
            raise ValueError("曲目最多支持 100000 小节")
        validate_bpm(self.bpm)
        validate_meter(self.meter)
        if type(self.detect_changes) is not bool:
            raise ValueError("detect_changes must be a boolean")
        if not isinstance(self.events, tuple) or not isinstance(self.manual_sections, tuple):
            raise ValueError("events and manual_sections must be tuples")
        for key in (self.forced_key, self.original_key):
            if key is not None:
                if not isinstance(key, MusicalKey):
                    raise ValueError("project key must be a MusicalKey or None")
                key.validate()
        ids: set[int] = set()
        onsets: set[tuple[int, int]] = set()
        for event in self.events:
            if not isinstance(event, ChordEvent):
                raise ValueError("events must contain ChordEvent objects")
            event.validate()
            if event.bar > self.bars or event.tick >= self.meter * PPQ:
                raise ValueError("event position is outside the project or bar")
            if event.id in ids:
                raise ValueError(f"duplicate event id: {event.id}")
            if (event.bar, event.tick) in onsets:
                raise ValueError(f"duplicate event onset: bar {event.bar}, tick {event.tick}")
            ids.add(event.id)
            onsets.add((event.bar, event.tick))
        section_bars: set[int] = set()
        for section in self.manual_sections:
            if not isinstance(section, KeySection):
                raise ValueError("manual_sections must contain KeySection objects")
            section.validate()
            if section.first_bar > self.bars:
                raise ValueError("section is outside the project")
            if section.first_bar in section_bars:
                raise ValueError(f"duplicate manual section bar: {section.first_bar}")
            section_bars.add(section.first_bar)
        if self.loop is not None:
            if not isinstance(self.loop, LoopRange):
                raise ValueError("loop must be a LoopRange or None")
            self.loop.validate()
            if self.loop.end_bar_exclusive > self.bars + 1:
                raise ValueError("loop is outside the project")


def event_position(event: ChordEvent) -> dict[str, int]:
    """Convert an event to canonical, one-based legacy position components."""
    event.validate()
    beat, within_beat = divmod(event.tick, PPQ)
    division, tick = divmod(within_beat, PPQ // 4)
    return {"bar": event.bar, "beat": beat + 1, "division": division + 1, "tick": tick}
