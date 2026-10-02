"""Port of Sources/ChordTheory.swift; no UI or host dependencies.

The Krumhansl–Kessler profiles, chord compatibility, duration weighting,
harmonic-minor dominant treatment, and offline family-state decoder are kept
from the Swift implementation. See THIRD_PARTY_NOTICES.md for attribution.
"""

from dataclasses import dataclass
from functools import lru_cache
from math import sqrt

from .models import ChartDocument, ChordEvent, KeySection, MusicalKey, PPQ

CHROMATIC = ("C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B")
_SHARP_NAMES = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
_FLAT_NAMES = ("C", "D♭", "D", "E♭", "E", "F", "G♭", "G", "A♭", "A", "B♭", "B")
_BASE_NOTES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_DEGREES = ("1", "♭2", "2", "♭3", "3", "4", "♯4", "5", "♭6", "6", "♭7", "7")
_SUPERSCRIPTS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶₇⁸⁹")
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _parsed_root(text: str) -> tuple[int, str] | None:
    trimmed = text.strip(" \t")
    if not trimmed or trimmed[0].upper() not in _BASE_NOTES:
        return None
    remainder = trimmed[1:]
    accidental = 0
    if remainder and remainder[0] in "#♯b♭":
        accidental = 1 if remainder[0] in "#♯" else -1
        remainder = remainder[1:]
    elif remainder.upper().startswith(" SHARP"):
        accidental, remainder = 1, remainder[6:]
    elif remainder.upper().startswith(" FLAT"):
        accidental, remainder = -1, remainder[5:]
    return (_BASE_NOTES[trimmed[0].upper()] + accidental) % 12, remainder


def note_name(pitch: int, prefer_flats: bool = False) -> str:
    return (_FLAT_NAMES if prefer_flats else _SHARP_NAMES)[pitch % 12]


def transpose(symbol: str, semitones: int, prefer_flats: bool = False) -> str:
    if symbol == "N.C." or semitones % 12 == 0:
        return symbol
    parts = symbol.split("/", 1)
    main = _parsed_root(parts[0])
    if main is None:
        return symbol
    result = note_name(main[0] + semitones, prefer_flats) + main[1]
    if len(parts) == 2:
        bass = _parsed_root(parts[1])
        result += "/" + (note_name(bass[0] + semitones, prefer_flats) + bass[1]
                         if bass is not None else parts[1])
    return result


def display_chord(symbol: str) -> str:
    parts = symbol.split("/", 1)
    main = _parsed_root(parts[0])
    if main is None or "7" not in main[1]:
        return symbol
    # Slice the original spelling exactly as Swift does, preserving accidentals.
    root = parts[0][:len(parts[0]) - len(main[1])]
    return root + main[1].replace("7", "₇") + ("/" + parts[1] if len(parts) == 2 else "")


def number(symbol: str, key: MusicalKey) -> str:
    if symbol == "N.C.":
        return symbol
    parts = symbol.split("/", 1)
    main = _parsed_root(parts[0])
    if main is None:
        return symbol
    interval = (main[0] - key.major_family_root) % 12
    quality = main[1].translate(_SUPERSCRIPTS).replace("b⁵", "b5")
    result = _DEGREES[interval] + quality
    if len(parts) == 2:
        bass = _parsed_root(parts[1])
        result += "/" + (_DEGREES[(bass[0] - key.major_family_root) % 12] + bass[1]
                         if bass is not None else parts[1])
    return result


def parse_key(text: str) -> MusicalKey | None:
    parsed = _parsed_root(text)
    if parsed is None:
        return None
    quality = parsed[1].strip().lower()
    if quality not in ("", "m", "minor", "小调"):
        return None
    return MusicalKey(parsed[0], bool(quality))


@dataclass(frozen=True)
class _Evidence:
    start: float
    end: float
    root: int
    intervals: tuple[int, ...]
    minor: bool
    dominant: bool
    has_third: bool


def _make_evidence(events: tuple[ChordEvent, ...], meter: int, last_bar: int,
                   bar_starts: tuple[float, ...] = ()) -> tuple[_Evidence, ...]:
    evidence = []
    for index, event in enumerate(events):
        chord = _parsed_root(event.symbol.split("/")[0])
        if chord is None:
            continue
        quality = chord[1].lower().replace(" ", "")
        minor = quality.startswith("m") and not quality.startswith("maj")
        diminished = any(token in quality for token in ("dim", "°", "ø"))
        suspended = "sus" in quality or quality == "5"
        augmented = "aug" in quality or quality.startswith("+")
        tones = [0, 3 if minor or diminished else 4, 6 if diminished else 8 if augmented else 7]
        if suspended:
            tones = [0, 2, 7] if "sus2" in quality else [0, 7] if quality == "5" else [0, 5, 7]
        if "b5" in quality:
            tones = [6 if tone == 7 else tone for tone in tones]
        if "#5" in quality:
            tones = [8 if tone == 7 else tone for tone in tones]
        extended = any(token in quality for token in ("7", "9", "11", "13")) and "add" not in quality
        if extended:
            tones.append(11 if "maj" in quality else 9 if diminished and "ø" not in quality and not minor else 10)
        if "6" in quality or "13" in quality:
            tones.append(9)
        if "9" in quality:
            tones.append(1 if "b9" in quality else 3 if "#9" in quality else 2)
        if "11" in quality:
            tones.append(6 if "#11" in quality else 5)
        start = (bar_starts[event.bar-1] if bar_starts else (event.bar-1)*meter) + event.tick/PPQ
        if index + 1 < len(events):
            following = events[index + 1]
            end = (bar_starts[following.bar-1] if bar_starts else (following.bar-1)*meter) + following.tick/PPQ
        else:
            end = bar_starts[last_bar] if bar_starts else float(last_bar*meter)
        if end > start:
            evidence.append(_Evidence(start, end, chord[0], tuple(sorted(set(tones))), minor,
                                      extended and not minor and not diminished and not suspended and "maj" not in quality,
                                      not suspended and not diminished))
    return tuple(evidence)


def _score(key: MusicalKey, evidence: tuple[_Evidence, ...], start: float, end: float) -> float:
    scale = (0, 2, 3, 5, 7, 8, 10) if key.is_minor else (0, 2, 4, 5, 7, 9, 11)
    histogram = [0.0] * 12
    compatibility = duration = 0.0
    for index, chord in enumerate(evidence):
        length = max(0, min(end, chord.end) - max(start, chord.start))
        if length <= 0:
            continue
        root = (chord.root - key.root) % 12
        harmonic_dominant = key.is_minor and root == 7 and not chord.minor and chord.has_third
        fitting = sum((root + tone) % 12 in scale or (harmonic_dominant and (root + tone) % 12 == 11)
                      for tone in chord.intervals)
        value = 4 * fitting / len(chord.intervals) - 2
        if root in scale:
            value += 0.5
        if root == 0 and chord.has_third and chord.minor == key.is_minor:
            value += 0.8
        if harmonic_dominant:
            value += 0.8
        if index > 0 and chord.start >= start and root == 0 and chord.has_third and chord.minor == key.is_minor:
            before = evidence[index - 1]
            if before.dominant and (before.root - chord.root) % 12 == 7:
                value += 0.8
        compatibility += length * value
        duration += length
        for tone in chord.intervals:
            histogram[(chord.root + tone) % 12] += length
    if duration <= 0:
        return 0.0
    profile = _MINOR_PROFILE if key.is_minor else _MAJOR_PROFILE
    h_mean, p_mean = sum(histogram) / 12, sum(profile) / 12
    numerator = h_variance = p_variance = 0.0
    for pitch in range(12):
        h, p = histogram[pitch] - h_mean, profile[(pitch - key.root) % 12] - p_mean
        numerator += h * p
        h_variance += h * h
        p_variance += p * p
    correlation = numerator / sqrt(h_variance * p_variance) if h_variance > 0 else 0
    return compatibility / duration + 2 * correlation


@lru_cache(maxsize=32)
def _analyzed_sections(chords: tuple[ChordEvent, ...], forced_key: MusicalKey | None,
                       detect_changes: bool, meter: int,
                       bar_starts: tuple[float, ...] = ()) -> tuple[KeySection, ...]:
    if forced_key is not None:
        return (KeySection(1, forced_key),)
    fallback = (KeySection(1, MusicalKey(0)),)
    if not chords:
        return fallback
    events = tuple(sorted(chords, key=lambda event: (event.bar, event.tick)))
    last_bar = events[-1].bar
    boundaries = bar_starts or tuple(float(bar*meter) for bar in range(last_bar+1))
    evidence = _make_evidence(events, meter, last_bar, bar_starts)
    if not evidence:
        return fallback
    candidates = tuple(MusicalKey(root, minor) for root in range(12) for minor in (False, True))
    global_scores = [_score(key, evidence, 0, boundaries[last_bar]) for key in candidates]
    # Python max, like Swift's ordered Sequence.max, retains the first equal item.
    global_index = max(range(24), key=global_scores.__getitem__)
    global_key = candidates[global_index]
    if not detect_changes or last_bar < 8:
        return (KeySection(1, global_key),)
    families = [[index for index, key in enumerate(candidates) if key.major_family_root == family]
                for family in range(12)]
    emissions = [[0.0] * 12 for _ in range(last_bar)]
    for bar in range(last_bar):
        local = [_score(key, evidence, boundaries[bar], boundaries[bar+1]) for key in candidates]
        for family in range(12):
            emissions[bar][family] = max(local[index] for index in families[family])
            emissions[bar][family] += 0.15 * max(global_scores[index] for index in families[family])
    previous = emissions[0]
    back = [[0] * 12 for _ in range(last_bar)]
    for bar in range(1, last_bar):
        following = [0.0] * 12
        for family in range(12):
            best = max(range(12), key=lambda prior: previous[prior] - (0 if prior == family else 10))
            following[family] = previous[best] - (0 if best == family else 10) + emissions[bar][family]
            back[bar][family] = best
        previous = following
    path = [global_key.major_family_root] * last_bar
    path[-1] = max(range(12), key=previous.__getitem__)
    for bar in range(last_bar - 1, 0, -1):
        path[bar - 1] = back[bar][path[bar]]
    start = 0
    while start < last_bar:
        end = start + 1
        while end < last_bar and path[end] == path[start]:
            end += 1
        roots = {chord.root for chord in evidence if chord.start < boundaries[end] and chord.end > boundaries[start]}
        if end - start < 4 or len(roots) < 3:
            neighbors = {path[start - 1] if start > 0 else global_key.major_family_root,
                         path[end] if end < last_bar else global_key.major_family_root}
            # Swift iterates an unordered Set here. Sort so exact ties cannot
            # depend on a process hash seed; lower numbered family wins.
            replacement = max(sorted(neighbors), key=lambda family: sum(row[family] for row in emissions[start:end]))
            path[start:end] = [replacement] * (end - start)
        start = end
    result = []
    start = 0
    while start < last_bar:
        end = start + 1
        while end < last_bar and path[end] == path[start]:
            end += 1
        index = max(families[path[start]],
                    key=lambda candidate: _score(candidates[candidate], evidence, boundaries[start], boundaries[end]))
        result.append(KeySection(start + 1, candidates[index]))
        start = end
    return tuple(result)


def analyze_sections(document: ChartDocument) -> tuple[KeySection, ...]:
    """Analyze chord extents, then apply the original manual-section semantics.

    Manual entries replace the automatic map, preserving only its initial key
    before the first manual change. They also override a forced key at bar 1.
    Empty project tail bars do not change the evidence duration, matching Swift.
    """
    document.validate()
    if document.score is not None:
        if document.forced_key is not None:
            return (KeySection(1, document.forced_key),)
        source_indices = {measure.id: index for index, measure in enumerate(document.score.measures, 1)}
        keys = {}
        for change in document.score.key_changes:
            if change.mode != "unknown":
                keys[source_indices[change.measure_id]] = MusicalKey(
                    (7 * change.fifths + (9 if change.mode == "minor" else 0)) % 12,
                    change.mode == "minor")
        # The legacy display adapter needs a starting key. Score rendering uses
        # the source map directly and labels absent keys as unknown.
        if 1 not in keys:
            keys[1] = MusicalKey(0)
        return tuple(KeySection(bar, key) for bar, key in sorted(keys.items()))
    boundaries: tuple[float, ...] = ()
    if document.timing_changes and document.events:
        from .timing import measure_quarters
        values = [0.0]
        for bar in range(1, max(event.bar for event in document.events)+1):
            values.append(values[-1] + float(measure_quarters(document, bar)))
        boundaries = tuple(values)
    automatic = _analyzed_sections(document.events, document.forced_key, document.detect_changes,
                                   document.meter, boundaries)
    if not document.manual_sections:
        return automatic
    result = [KeySection(1, automatic[0].key)]
    for section in sorted(document.manual_sections, key=lambda section: section.first_bar):
        if section.first_bar == result[-1].first_bar:
            result[-1] = section
        else:
            result.append(section)
    return tuple(result)
