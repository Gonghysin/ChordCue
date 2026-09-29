from dataclasses import replace

import pytest

from chordcue.models import ChartDocument, ChordEvent, KeySection, MusicalKey
from chordcue.parsing import parse_manual
from chordcue.project import demo_document
from chordcue.theory import analyze_sections, display_chord, note_name, number, parse_key, transpose


@pytest.mark.parametrize("symbol,shift,flat,expected", [
    ("F#m7(b5)/B", 2, False, "G♯m7(b5)/C♯"), ("Cmaj7/E", 1, True, "D♭maj7/F"),
    ("N.C.", 3, False, "N.C."), ("Bb7/F", 0, False, "Bb7/F"), ("C/xyz", 2, False, "D/xyz"),
    ("C/foo", 2, False, "D/Goo"),
    ("C/B", -2, False, "A♯/A"), ("Not a chord", 2, False, "Not a chord"),
    ("F SHARPm/A FLAT", 1, True, "Gm/A"), ("Cb", 1, False, "C"), ("B#", -1, True, "B"),
])
def test_transpose_preserves_quality_and_slash_bass(symbol, shift, flat, expected):
    assert transpose(symbol, shift, flat) == expected


def test_note_names_wrap_and_enharmonic_preferences():
    assert note_name(-1) == "B"
    assert note_name(25, True) == "D♭"
    assert note_name(25) == "C♯"


@pytest.mark.parametrize("symbol,expected", [("Am7/E", "6m₇/3"), ("Bm7(b5)/F", "7m₇(b5)/4"),
                                              ("G13(b9)/B", "5¹³(b⁹)/7"), ("F#7", "♯4₇"),
                                              ("N.C.", "N.C."), ("bad?", "7ad?")])
def test_minor_numbering_uses_relative_major_family(symbol, expected):
    assert number(symbol, MusicalKey(9, True)) == expected
    assert number(symbol, MusicalKey(0)) == expected


def test_display_only_changes_sevenths_in_main_quality():
    assert display_chord("Bbmaj7/G7") == "Bbmaj₇/G7"
    assert display_chord("Dm9") == "Dm9"
    assert display_chord("N.C.") == "N.C."


@pytest.mark.parametrize("text,expected", [("C", MusicalKey(0)), ("Db", MusicalKey(1)),
                                           ("f♯m", MusicalKey(6, True)), ("A minor", MusicalKey(9, True)),
                                           ("B 小调", MusicalKey(11, True)), ("C major", None),
                                           ("Cmaj7", None), ("H", None), ("", None)])
def test_key_parser(text, expected):
    assert parse_key(text) == expected


def test_empty_and_unrecognized_chords_fall_back_to_c():
    assert analyze_sections(ChartDocument()) == (KeySection(1, MusicalKey(0)),)
    assert analyze_sections(ChartDocument(events=(ChordEvent(0, 1, 0, "N.C."),))) == (KeySection(1, MusicalKey(0)),)


def test_global_minor_harmonic_dominant_and_demo():
    document = ChartDocument(events=parse_manual("1 Am\n2 Dm\n3 E7\n4 Am"))
    assert analyze_sections(document) == (KeySection(1, MusicalKey(9, True)),)
    assert analyze_sections(demo_document()) == (KeySection(1, MusicalKey(0)),)


def _modulation_document():
    return ChartDocument(events=parse_manual(
        "1 C\n2 F\n3 G7\n4 C\n5 C\n6 F\n7 G7\n8 C\n"
        "9 D\n10 G\n11 A7\n12 D\n13 D\n14 G\n15 A7\n16 D"))


def test_supported_four_plus_bar_modulation_creates_boundary():
    document = _modulation_document()
    assert analyze_sections(document) == (KeySection(1, MusicalKey(0)), KeySection(9, MusicalKey(2)))
    assert len(analyze_sections(replace(document, detect_changes=False))) == 1
    assert analyze_sections(replace(document, bars=128)) == analyze_sections(document)


def test_relative_major_minor_cannot_introduce_numbered_key_change():
    document = ChartDocument(events=parse_manual(
        "1 C\n2 F\n3 G7\n4 C\n5 C\n6 F\n7 G7\n8 C\n"
        "9 Am\n10 Dm\n11 E7\n12 Am\n13 Am\n14 Dm\n15 E7\n16 Am"))
    assert {section.key.major_family_root for section in analyze_sections(document)} == {0}


def test_short_tonicization_is_absorbed():
    document = ChartDocument(events=parse_manual(
        "1 C\n2 F\n3 G7\n4 C\n5 C\n6 F\n7 G7\n8 C\n"
        "9 D\n10 G\n11 A7\n12 C\n13 F\n14 G7\n15 C\n16 C"))
    assert analyze_sections(document) == (KeySection(1, MusicalKey(0)),)


def test_manual_sections_replace_automatic_map_and_override_forced_start():
    document = _modulation_document()
    forced = replace(document, forced_key=MusicalKey(6))
    assert analyze_sections(forced) == (KeySection(1, MusicalKey(6)),)
    manual = replace(document, manual_sections=(KeySection(12, MusicalKey(5)),))
    assert analyze_sections(manual) == (KeySection(1, MusicalKey(0)), KeySection(12, MusicalKey(5)))
    both = replace(forced, manual_sections=(KeySection(1, MusicalKey(9, True)), KeySection(5, MusicalKey(4))))
    assert analyze_sections(both) == both.manual_sections


def test_analysis_cache_depends_on_all_theory_inputs_and_events_are_order_independent():
    document = _modulation_document()
    baseline = analyze_sections(document)
    assert analyze_sections(replace(document, events=tuple(reversed(document.events)))) == baseline
    assert analyze_sections(replace(document, forced_key=MusicalKey(3))) != baseline
    assert analyze_sections(document) == baseline
    assert analyze_sections(replace(document, meter=3)) == baseline
