"""Immutable, vendor-independent score data; offsets/durations are quarter notes.

The source score is never expanded into repeat occurrences or inferred chords.
See docs/ISSUE1_CONTRACT.md for the shared Python/Swift/JSON contract.
"""

from dataclasses import dataclass, fields, is_dataclass
from fractions import Fraction
from functools import lru_cache
from math import gcd, isfinite
import re
from types import UnionType
from typing import Any, cast, get_args, get_origin, get_type_hints

MAX_SCORE_BYTES = 32 * 1024 * 1024
MAX_MEASURES = 10_000
MAX_PARTS = 64
MAX_EVENTS = 500_000
MAX_NOTES = 1_000_000
MAX_CHORDS = 100_000
MAX_WARNINGS = 10_000
MAX_JSON_NODES = 2_000_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")


def _text(value: object, name: str, maximum: int, *, empty: bool = False) -> None:
    if (not isinstance(value, str) or len(value) > maximum
            or (not empty and not value.strip())
            or any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF
                   or c in "\u2028\u2029" for c in value)):
        raise ValueError(f"{name} must be single-line text of at most {maximum} characters")


def _id(value: object, name: str = "id") -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name} must be a stable ID of 1..128 characters")


def _choice(value: object, name: str, choices: tuple[str, ...]) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"unsupported {name}: {value!r}")


def _boolean(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be boolean")


def _instance(value: object, cls: type, name: str) -> None:
    if not isinstance(value, cls):
        raise ValueError(f"{name} must be {cls.__name__}")
    getattr(value, "validate")()


def _tuple(value: object, cls: type, name: str, maximum: int, minimum: int = 0) -> None:
    if not isinstance(value, tuple) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} must be a tuple with {minimum}..{maximum} entries")
    for item in value:
        _instance(item, cls, name)


class _Validated:
    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class QuarterFraction(_Validated):
    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        self.validate()
        divisor = gcd(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", self.numerator // divisor)
        object.__setattr__(self, "denominator", self.denominator // divisor)

    def validate(self) -> None:
        _integer(self.numerator, "fraction numerator", -(2**31 - 1), 2**31 - 1)
        _integer(self.denominator, "fraction denominator", 1, 1_000_000)

    def as_fraction(self) -> Fraction:
        self.validate()
        return Fraction(self.numerator, self.denominator)


ZERO = QuarterFraction(0)


def _fraction(value: object, name: str, *, positive: bool = False) -> Fraction:
    _instance(value, QuarterFraction, name)
    result = getattr(value, "as_fraction")()
    if result < 0 or (positive and result == 0) or result > 4096:
        raise ValueError(f"{name} is outside its permitted quarter-note range")
    return result


@dataclass(frozen=True)
class ScoreMeter(_Validated):
    numerator: int = 4
    denominator: int = 4

    def validate(self) -> None:
        _integer(self.numerator, "meter numerator", 1, 64)
        _integer(self.denominator, "meter denominator", 1, 64)
        if self.denominator not in (1, 2, 4, 8, 16, 32, 64):
            raise ValueError("meter denominator must be a power of two")

    @property
    def quarters(self) -> Fraction:
        return Fraction(self.numerator * 4, self.denominator)


@dataclass(frozen=True)
class ScoreSource(_Validated):
    format: str
    file_name: str | None = None
    sha256: str | None = None

    def validate(self) -> None:
        _choice(self.format, "source format", ("musicxml", "guitarpro", "manual"))
        if self.file_name is not None:
            _text(self.file_name, "source filename", 1024)
            if "/" in self.file_name or "\\" in self.file_name:
                raise ValueError("source filename must be a basename")
        if self.sha256 is not None and (not isinstance(self.sha256, str)
                                       or not _HEX.fullmatch(self.sha256)):
            raise ValueError("source sha256 must be lowercase hex SHA-256")


@dataclass(frozen=True)
class ScoreMarker(_Validated):
    id: str
    kind: str
    label: str = ""
    offset: QuarterFraction = ZERO

    def validate(self) -> None:
        _id(self.id)
        _choice(self.kind, "marker kind", ("section", "rehearsal", "segno", "coda", "fine"))
        _text(self.label, "marker label", 1024, empty=True)
        _fraction(self.offset, "marker offset")


@dataclass(frozen=True)
class ScoreNavigation(_Validated):
    kind: str
    target_marker_id: str | None = None
    offset: QuarterFraction = ZERO

    def validate(self) -> None:
        _choice(self.kind, "navigation kind", ("dc", "ds", "toCoda", "fine"))
        if self.target_marker_id is not None:
            _id(self.target_marker_id, "navigation target")
        _fraction(self.offset, "navigation offset")


@dataclass(frozen=True)
class ScoreMeasure(_Validated):
    id: str
    number: str
    duration: QuarterFraction
    meter: ScoreMeter = ScoreMeter()
    repeat_start: bool = False
    repeat_end: int | None = None
    ending_numbers: tuple[int, ...] = ()
    markers: tuple[ScoreMarker, ...] = ()
    navigation: tuple[ScoreNavigation, ...] = ()

    def validate(self) -> None:
        _id(self.id)
        _text(self.number, "source measure number", 128)
        duration = _fraction(self.duration, "measure duration", positive=True)
        _instance(self.meter, ScoreMeter, "measure meter")
        _boolean(self.repeat_start, "repeat start")
        if self.repeat_end is not None:
            _integer(self.repeat_end, "repeat end traversal count", 2, 32)
        if not isinstance(self.ending_numbers, tuple) or len(self.ending_numbers) > 32:
            raise ValueError("ending numbers must be a bounded tuple")
        for number in self.ending_numbers:
            _integer(number, "ending number", 1, 32)
        if len(set(self.ending_numbers)) != len(self.ending_numbers):
            raise ValueError("duplicate ending number")
        _tuple(self.markers, ScoreMarker, "measure markers", 128)
        _tuple(self.navigation, ScoreNavigation, "measure navigation", 16)
        for marker in self.markers:
            if marker.offset.as_fraction() > duration:
                raise ValueError("marker/navigation offset outside source measure")
        for navigation in self.navigation:
            if navigation.offset.as_fraction() > duration:
                raise ValueError("marker/navigation offset outside source measure")


@dataclass(frozen=True)
class ScoreBendPoint(_Validated):
    position: float
    semitones: float

    def validate(self) -> None:
        for value, name, low, high in ((self.position, "curve position", 0, 1),
                                       (self.semitones, "curve semitones", -24, 24)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not isfinite(value):
                raise ValueError(f"invalid {name}")


TECHNIQUE_DIRECTIONS = {
    "slide": ("shift", "legato", "inAbove", "inBelow", "outUp", "outDown"),
    "bend": ("bend", "prebend", "release", "prebendRelease"),
    "whammy": ("bend", "prebend", "release", "prebendRelease"),
    "vibrato": ("slight", "wide"),
    "harmonic": ("natural", "artificial", "tap", "pinch", "semi", "feedback"),
    "pick": ("up", "down"), "brush": ("up", "down", "arpeggioUp", "arpeggioDown"),
}
TECHNIQUE_KINDS = ("hammerOn", "pullOff", "slide", "bend", "vibrato", "harmonic", "palmMute",
                   "deadNote", "letRing", "staccato", "accent", "heavyAccent", "pick",
                   "tremoloPicking", "trill", "tap", "slap", "pop", "ghost", "brush", "whammy")
EVENT_TECHNIQUES = ("vibrato", "pick", "tremoloPicking", "tap", "slap", "pop", "brush", "whammy")


@dataclass(frozen=True)
class ScoreTechnique(_Validated):
    kind: str
    target_note_id: str | None = None
    value: float | None = None
    direction: str | None = None
    curve: tuple[ScoreBendPoint, ...] = ()

    def validate(self) -> None:
        _choice(self.kind, "technique kind", TECHNIQUE_KINDS)
        if self.target_note_id is not None:
            _id(self.target_note_id, "technique target note id")
            if self.kind not in ("hammerOn", "pullOff", "slide") or (self.kind == "slide" and self.direction not in ("shift", "legato")):
                raise ValueError("technique cannot have a target note")
        if self.kind in TECHNIQUE_DIRECTIONS:
            _choice(self.direction, "technique direction", TECHNIQUE_DIRECTIONS[self.kind])
        elif self.direction is not None:
            raise ValueError("technique cannot have a direction")
        _tuple(self.curve, ScoreBendPoint, "technique curve", 64)
        if self.kind in ("bend", "whammy"):
            if len(self.curve) < 2 or self.curve[0].position != 0 or self.curve[-1].position != 1:
                raise ValueError("bend/whammy curve must span 0..1")
            if any(a.position > b.position for a, b in zip(self.curve, self.curve[1:])):
                raise ValueError("curve positions must be nondecreasing")
        elif self.curve:
            raise ValueError("technique cannot have a curve")
        if self.value is not None:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not 0 <= self.value <= 256 or not isfinite(self.value):
                raise ValueError("technique value must be finite")
            if self.kind == "harmonic":
                valid = 0 <= self.value <= 99
            elif self.kind == "trill":
                valid = self.value == int(self.value) and 0 <= self.value <= 127
            elif self.kind == "tremoloPicking":
                valid = self.value in (8, 16, 32, 64, 128, 256)
            elif self.kind == "brush":
                valid = 0 <= self.value <= 16
            else:
                valid = False
            if not valid:
                raise ValueError("invalid technique value for kind")
        elif self.kind == "tremoloPicking":
            raise ValueError("tremoloPicking requires subdivision value")


def _techniques(items: tuple[ScoreTechnique, ...], *, event: bool = False) -> None:
    _tuple(items, ScoreTechnique, "techniques", 32)
    if event and any(t.kind not in EVENT_TECHNIQUES for t in items):
        raise ValueError("note technique cannot belong to an event")
    if not event and any(t.kind in ("brush", "whammy", "tremoloPicking") for t in items):
        raise ValueError("event technique cannot belong to a note")
    if len(set((t.kind, t.direction) for t in items)) != len(items):
        raise ValueError("duplicate technique kind/direction")


@dataclass(frozen=True)
class ScoreNote(_Validated):
    id: str
    pitch: int | None = None
    string: int | None = None
    fret: int | None = None
    tie_start: bool = False
    tie_stop: bool = False
    unpitched: bool = False
    accidental: str | None = None
    written_pitch: int | None = None
    techniques: tuple[ScoreTechnique, ...] = ()

    def validate(self) -> None:
        _id(self.id)
        for value, name in ((self.tie_start, "tie start"), (self.tie_stop, "tie stop"),
                            (self.unpitched, "unpitched")):
            _boolean(value, name)
        if self.pitch is not None:
            _integer(self.pitch, "MIDI pitch", 0, 127)
        if (self.string is None) != (self.fret is None):
            raise ValueError("TAB string and fret must be supplied together")
        if self.string is not None:
            _integer(self.string, "TAB string", 1, 24)
            _integer(self.fret, "TAB fret", 0, 99)
        if self.pitch is None and not self.unpitched and self.string is None:
            raise ValueError("note needs a MIDI pitch, TAB position, or unpitched flag")
        if self.accidental is not None:
            _text(self.accidental, "accidental", 64)
        if self.written_pitch is not None:
            _integer(self.written_pitch, "written MIDI pitch", 0, 127)
        _techniques(self.techniques)


@dataclass(frozen=True)
class ScoreEvent(_Validated):
    id: str
    measure_id: str
    offset: QuarterFraction
    duration: QuarterFraction
    voice: int = 1
    is_rest: bool = False
    grace: bool = False
    notes: tuple[ScoreNote, ...] = ()
    techniques: tuple[ScoreTechnique, ...] = ()

    def validate(self) -> None:
        _id(self.id)
        _id(self.measure_id, "event measure id")
        _fraction(self.offset, "event offset")
        _boolean(self.is_rest, "rest")
        _boolean(self.grace, "grace")
        _fraction(self.duration, "event duration", positive=not self.grace)
        _integer(self.voice, "voice", 1, 16)
        _tuple(self.notes, ScoreNote, "event notes", 128)
        if self.is_rest == bool(self.notes) or (self.grace and self.is_rest):
            raise ValueError("rest must have no notes; non-rest must have notes")
        _techniques(self.techniques, event=True)


@dataclass(frozen=True)
class ScoreStaff(_Validated):
    id: str
    name: str = ""
    kind: str = "standard"
    clef: str | None = None
    tuning: tuple[int, ...] = ()
    capo: int = 0
    events: tuple[ScoreEvent, ...] = ()

    def validate(self) -> None:
        _id(self.id)
        _text(self.name, "staff name", 1024, empty=True)
        _choice(self.kind, "staff kind", ("standard", "tab", "percussion"))
        if self.clef is not None:
            _text(self.clef, "clef", 64)
        if not isinstance(self.tuning, tuple) or len(self.tuning) > 24:
            raise ValueError("tuning must be a tuple of up to 24 MIDI pitches")
        for pitch in self.tuning:
            _integer(pitch, "tuning pitch", 0, 127)
        _integer(self.capo, "capo", 0, 24)
        _tuple(self.events, ScoreEvent, "staff events", MAX_EVENTS)


@dataclass(frozen=True)
class ScoreChord(_Validated):
    id: str
    measure_id: str
    offset: QuarterFraction
    text: str
    staff_id: str | None = None

    def validate(self) -> None:
        _id(self.id)
        _id(self.measure_id, "chord measure id")
        _fraction(self.offset, "chord offset")
        _text(self.text, "original chord annotation", 1024)
        if self.staff_id is not None:
            _id(self.staff_id, "chord staff id")


@dataclass(frozen=True)
class ScorePart(_Validated):
    id: str
    name: str
    instrument: str | None = None
    staves: tuple[ScoreStaff, ...] = ()
    chords: tuple[ScoreChord, ...] = ()

    def validate(self) -> None:
        _id(self.id)
        _text(self.name, "part name", 1024)
        if self.instrument is not None:
            _text(self.instrument, "instrument", 1024)
        _tuple(self.staves, ScoreStaff, "part staves", 16, 1)
        _tuple(self.chords, ScoreChord, "part chords", MAX_CHORDS)


@dataclass(frozen=True)
class ScoreTempoChange(_Validated):
    measure_id: str
    offset: QuarterFraction
    bpm: float

    def validate(self) -> None:
        _id(self.measure_id, "tempo measure id")
        _fraction(self.offset, "tempo offset")
        if isinstance(self.bpm, bool) or not isinstance(self.bpm, (int, float)):
            raise ValueError("tempo must be a finite quarter-note BPM")
        try:
            valid = isfinite(self.bpm) and 1 <= self.bpm <= 1000
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("tempo must be finite and in 1..1000 BPM")


@dataclass(frozen=True)
class ScoreKeyChange(_Validated):
    measure_id: str
    offset: QuarterFraction
    fifths: int
    mode: str = "unknown"

    def validate(self) -> None:
        _id(self.measure_id, "key measure id")
        _fraction(self.offset, "key offset")
        _integer(self.fifths, "key fifths", -7, 7)
        _choice(self.mode, "key mode", ("major", "minor", "unknown"))


@dataclass(frozen=True)
class ScoreWarning(_Validated):
    code: str
    message: str
    severity: str = "warning"
    measure_id: str | None = None
    part_id: str | None = None

    def validate(self) -> None:
        _id(self.code, "warning code")
        _text(self.message, "warning message", 2048)
        _choice(self.severity, "warning severity", ("info", "warning"))
        if self.measure_id is not None:
            _id(self.measure_id, "warning measure id")
        if self.part_id is not None:
            _id(self.part_id, "warning part id")


@dataclass(frozen=True)
class ScoreIR(_Validated):
    id: str
    title: str
    source: ScoreSource
    measures: tuple[ScoreMeasure, ...]
    parts: tuple[ScorePart, ...]
    tempo_changes: tuple[ScoreTempoChange, ...] = ()
    key_changes: tuple[ScoreKeyChange, ...] = ()
    warnings: tuple[ScoreWarning, ...] = ()
    format_version: int = 2

    def validate(self) -> None:
        _id(self.id)
        _text(self.title, "score title", 1024)
        _integer(self.format_version, "score formatVersion", 2, 2)
        _instance(self.source, ScoreSource, "score source")
        _tuple(self.measures, ScoreMeasure, "score measures", MAX_MEASURES, 1)
        _tuple(self.parts, ScorePart, "score parts", MAX_PARTS, 1)
        _tuple(self.tempo_changes, ScoreTempoChange, "tempo changes", MAX_CHORDS)
        _tuple(self.key_changes, ScoreKeyChange, "key changes", MAX_CHORDS)
        _tuple(self.warnings, ScoreWarning, "score warnings", MAX_WARNINGS)
        ids: set[str] = {self.id}

        def unique(identifier: str) -> None:
            if identifier in ids:
                raise ValueError(f"duplicate score id: {identifier}")
            ids.add(identifier)

        measures: dict[str, ScoreMeasure] = {}
        markers: dict[str, ScoreMarker] = {}
        for measure in self.measures:
            unique(measure.id)
            measures[measure.id] = measure
            for marker in measure.markers:
                unique(marker.id)
                markers[marker.id] = marker

        def position(measure_id: str, offset: QuarterFraction, *, at_end: bool = False) -> Fraction:
            if measure_id not in measures:
                raise ValueError(f"unknown source measure reference: {measure_id}")
            end = measures[measure_id].duration.as_fraction()
            point = offset.as_fraction()
            if point > end or (point == end and not at_end):
                raise ValueError("position outside source measure")
            return end

        count_events = count_notes = count_chords = count_techniques = 0
        note_locations = {}
        relationships: list[tuple[str, str]] = []
        measure_order = {m.id: i for i, m in enumerate(self.measures)}
        for part in self.parts:
            unique(part.id)
            staff_ids = {staff.id for staff in part.staves}
            for staff in part.staves:
                unique(staff.id)
                onsets: set[tuple[str, Fraction, int]] = set()
                count_events += len(staff.events)
                for event_index, event in enumerate(staff.events):
                    unique(event.id)
                    end = position(event.measure_id, event.offset, at_end=event.grace)
                    if event.offset.as_fraction() + event.duration.as_fraction() > end:
                        raise ValueError("event duration extends outside source measure")
                    onset = (event.measure_id, event.offset.as_fraction(), event.voice)
                    if not event.grace and onset in onsets:
                        raise ValueError("duplicate event onset in the same staff voice")
                    if not event.grace:
                        onsets.add(onset)
                    count_notes += len(event.notes)
                    count_techniques += len(event.techniques)
                    for note in event.notes:
                        unique(note.id)
                        count_techniques += len(note.techniques)
                        note_locations[note.id] = (staff.id, event.voice, measure_order[event.measure_id], event.offset.as_fraction(), note.string, event.grace, event_index)
                        relationships.extend((note.id, t.target_note_id) for t in note.techniques if t.target_note_id is not None)
                        if note.string is not None and staff.tuning and note.string > len(staff.tuning):
                            raise ValueError("TAB string is outside staff tuning")
            count_chords += len(part.chords)
            for chord in part.chords:
                unique(chord.id)
                position(chord.measure_id, chord.offset)
                if chord.staff_id is not None and chord.staff_id not in staff_ids:
                    raise ValueError("chord staff must belong to its part")
        if count_events > MAX_EVENTS or count_notes > MAX_NOTES or count_chords > MAX_CHORDS:
            raise ValueError("score exceeds its total event/note/chord complexity limit")
        if count_techniques > 200_000:
            raise ValueError("score exceeds total technique complexity limit")
        for origin, target in relationships:
            if target not in note_locations:
                raise ValueError("technique references an unknown target note")
            a, b = note_locations[origin], note_locations[target]
            if a[:2] != b[:2] or a[2:4] > b[2:4] or origin == target or (a[2:4] == b[2:4] and not (a[5] and a[6] < b[6])):
                raise ValueError("technique target must be later in the same staff voice")
            if a[4] is not None and b[4] is not None and a[4] != b[4]:
                raise ValueError("linked guitar technique must remain on the same string")
        for changes in (self.tempo_changes, self.key_changes):
            change_onsets: set[tuple[str, Fraction]] = set()
            for change in changes:
                position(change.measure_id, change.offset, at_end=True)
                change_onset = (change.measure_id, change.offset.as_fraction())
                if change_onset in change_onsets:
                    raise ValueError("duplicate tempo/key change onset")
                change_onsets.add(change_onset)
        part_ids = {part.id for part in self.parts}
        for warning in self.warnings:
            if warning.measure_id is not None and warning.measure_id not in measures:
                raise ValueError("warning references an unknown source measure")
            if warning.part_id is not None and warning.part_id not in part_ids:
                raise ValueError("warning references an unknown part")
        for measure in self.measures:
            for nav in measure.navigation:
                if nav.target_marker_id is not None and nav.target_marker_id not in markers:
                    raise ValueError("navigation references an unknown marker")
                if nav.kind in ("ds", "toCoda") and nav.target_marker_id is None:
                    if not any(w.measure_id == measure.id for w in self.warnings):
                        raise ValueError("unresolved navigation requires a measure warning")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result = _encode(self)
        check_json_complexity(result)
        return result

    @classmethod
    def from_dict(cls, data: object) -> "ScoreIR":
        check_json_complexity(data)
        if isinstance(data, dict) and type(data.get("formatVersion")) is int and data["formatVersion"] == 1:
            # Validate v1 strictly before adding the only new fields. Never infer
            # techniques that an old importer has already discarded.
            import copy
            _legacy_shape(cls, data, "score")
            data = copy.deepcopy(data)
            for part in data.get("parts", []):
                for staff in part.get("staves", []):
                    for event in staff.get("events", []):
                        if "techniques" in event:
                            raise ValueError("v1 fields differ from contract")
                        event["techniques"] = []
                        for note in event.get("notes", []):
                            if "techniques" in note or "writtenPitch" in note:
                                raise ValueError("v1 fields differ from contract")
                            note.update(writtenPitch=None, techniques=[])
            data["formatVersion"] = 2
        return _decode(cls, data, "score")


def check_json_complexity(data: object) -> None:
    """Bound work before constructing objects, rejecting non-JSON and nonfinite data."""
    stack = [(data, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > 32:
            raise ValueError("score/project JSON exceeds complexity limit")
        if isinstance(item, dict):
            if len(item) > 64:
                raise ValueError("score/project object has too many fields")
            for key, value in item.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise ValueError("JSON field must be bounded text")
                stack.append((value, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_NOTES:
                raise ValueError("score/project array exceeds complexity limit")
            stack.extend((value, depth + 1) for value in item)
        elif isinstance(item, str):
            if len(item) > 4096:
                raise ValueError("score/project JSON text exceeds limit")
        elif type(item) is int:
            if abs(item) > 2**53 - 1:
                raise ValueError("JSON integer exceeds exact cross-platform range")
        elif type(item) is float:
            if not isfinite(item):
                raise ValueError("non-finite JSON number")
        elif item is not None and type(item) is not bool:
            raise ValueError("score/project contains a non-JSON value")


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(item.capitalize() for item in tail)


def _legacy_shape(expected: Any, value: Any, path: str) -> None:
    """Reject missing/unknown v1 fields before performing a lossless migration."""
    origin = get_origin(expected)
    if origin is UnionType:
        if value is None:
            return
        expected = next(t for t in get_args(expected) if t is not type(None))
        origin = get_origin(expected)
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        for i, item in enumerate(value):
            _legacy_shape(get_args(expected)[0], item, f"{path}[{i}]")
    elif is_dataclass(expected):
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        names = {_camel(f.name): f.name for f in fields(expected)
                 if not (expected is ScoreNote and f.name in ("written_pitch", "techniques"))
                 and not (expected is ScoreEvent and f.name == "techniques")}
        if set(value) != set(names):
            raise ValueError(f"{path} v1 fields differ from contract")
        for key, name in names.items():
            _legacy_shape(_hints(cast(type, expected))[name], value[key], f"{path}.{key}")


def _encode(value: Any) -> Any:
    if is_dataclass(value):
        return {_camel(field.name): _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


@lru_cache(maxsize=32)
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _decode(expected: Any, value: Any, path: str) -> Any:
    origin = get_origin(expected)
    if origin is UnionType:
        if value is None and type(None) in get_args(expected):
            return None
        member = next(item for item in get_args(expected) if item is not type(None))
        return _decode(member, value, path)
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        return tuple(_decode(get_args(expected)[0], item, f"{path}[{index}]")
                     for index, item in enumerate(value))
    if is_dataclass(expected):
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        names = {_camel(field.name): field.name for field in fields(expected)}
        missing = names.keys() - value.keys()
        unknown = value.keys() - names.keys()
        if missing or unknown:
            raise ValueError(f"{path} fields differ from contract: missing {sorted(missing)}, "
                             f"unknown {sorted(unknown)}")
        cls = cast(type, expected)
        hints = _hints(cls)
        return cls(**{name: _decode(hints[name], value[key], f"{path}.{key}")
                           for key, name in names.items()})
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be a number")
        return value
    if type(value) is not expected:
        raise ValueError(f"{path} must be {expected.__name__}")
    return value
