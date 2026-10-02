"""Immutable, validated project data shared by every Windows component.

Positions use 960 ticks per quarter note.  Only serialization to the existing
browser protocol uses the older beat/division/tick representation.
"""

from dataclasses import dataclass
from math import isfinite

from .score_models import MAX_MEASURES, ScoreIR, ScoreMeter, ScoreTempoChange, ZERO

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
class TimingChange:
    """Effective timing from a source bar onward; BPM always counts quarters."""

    bar: int
    bpm: float
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _integer(self.bar, "timing bar", 1, 100_000)
        ScoreMeter(self.numerator, self.denominator)
        ScoreTempoChange("timing", ZERO, self.bpm)

    @property
    def meter(self) -> ScoreMeter:
        return ScoreMeter(self.numerator, self.denominator)


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
    score: ScoreIR | None = None
    selected_part_id: str | None = None
    timing_changes: tuple[TimingChange, ...] = ()

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
        if not isinstance(self.timing_changes, tuple) or len(self.timing_changes) > MAX_MEASURES:
            raise ValueError("timing_changes must be a bounded tuple")
        if self.timing_changes:
            if self.score is not None:
                raise ValueError("source score timing must be stored in ScoreIR")
            if self.bars > MAX_MEASURES:
                raise ValueError("有变化表的手工谱最多支持 10000 小节")
            previous_bar = 0
            for change in self.timing_changes:
                if not isinstance(change, TimingChange):
                    raise ValueError("timing_changes must contain TimingChange objects")
                change.validate()
                if change.bar <= previous_bar or change.bar > self.bars:
                    raise ValueError("timing bars must be unique, ordered, and within the project")
                previous_bar = change.bar
            if self.timing_changes[0].bar != 1:
                raise ValueError("timing table must start at bar 1")
        capacities = [self.meter * PPQ] * self.bars if self.timing_changes else None
        if capacities is not None:
            change_index = 0
            for bar in range(1, self.bars + 1):
                if (change_index + 1 < len(self.timing_changes)
                        and self.timing_changes[change_index + 1].bar == bar):
                    change_index += 1
                quarters = self.timing_changes[change_index].meter.quarters
                capacities[bar - 1] = int(quarters * PPQ)
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
            capacity = (capacities[event.bar - 1] if capacities is not None and event.bar <= self.bars
                        else self.meter * PPQ)
            if self.timing_changes and (event.bar > self.bars or event.tick >= capacity):
                raise ValueError(f"第 {event.bar} 小节的和弦（ID {event.id}，位置 {event.tick}/{PPQ} 四分音符）"
                                 f"超出小节容量 {capacity}/{PPQ} 四分音符")
            if event.bar > self.bars or event.tick >= capacity:
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
        if self.score is not None:
            if not isinstance(self.score, ScoreIR):
                raise ValueError("score must be a ScoreIR or None")
            self.score.validate()
            if self.bars != len(self.score.measures):
                raise ValueError("project bars must match source score measure count")
            if self.selected_part_id is not None and self.selected_part_id not in {
                    part.id for part in self.score.parts}:
                raise ValueError("selected part must reference a score part")
        elif self.selected_part_id is not None:
            raise ValueError("selected part requires a score")


def document_with_score(score: ScoreIR, selected_part_id: str | None = None) -> ChartDocument:
    """Create a legacy envelope while preserving the complete authoritative score.

    The legacy meter/BPM are display fallbacks. Score-aware playback must use the
    actual score maps; no chord symbols are inferred from notes here.
    """
    if not isinstance(score, ScoreIR):
        raise ValueError("score must be a ScoreIR")
    score.validate()
    first_meter = score.measures[0].meter
    fallback_meter = min(12, max(1, round(float(first_meter.quarters))))
    initial_tempo = next((change.bpm for change in score.tempo_changes
                          if change.measure_id == score.measures[0].id
                          and change.offset.numerator == 0), 120.0)
    return ChartDocument(name=score.title, bars=len(score.measures),
                         bpm=min(300.0, max(20.0, initial_tempo)), meter=fallback_meter,
                         score=score, selected_part_id=(score.parts[0].id if selected_part_id is None
                                                        else selected_part_id))


def event_position(event: ChordEvent) -> dict[str, int]:
    """Convert an event to canonical, one-based legacy position components."""
    event.validate()
    beat, within_beat = divmod(event.tick, PPQ)
    division, tick = divmod(within_beat, PPQ // 4)
    return {"bar": event.bar, "beat": beat + 1, "division": division + 1, "tick": tick}
