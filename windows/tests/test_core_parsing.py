import pytest

from chordcue.models import ChordEvent
from chordcue.parsing import format_manual, parse_logic_text, parse_manual
from chordcue.project import demo_document


def test_manual_sorts_without_renumbering_source_ids():
    events = parse_manual("3 Dm\n\n1 C\n2.3 G7/B")
    assert events == (ChordEvent(2, 1, 0, "C"), ChordEvent(3, 2, 1920, "G7/B"), ChordEvent(0, 3, 0, "Dm"))
    assert parse_manual("3 Dm\n\n1 C\n2.3 G7/B") == events


@pytest.mark.parametrize("line", ["1", "C 1", "0 C", "-1 C", "1.0 C", "1.5 C", "1.1.0.0 C",
                                    "1.1.5.0 C", "1.1.1.960 C", "1.4.4.240 C", "1.2.3 C",
                                    "1.1.1.1.1 C", "1..2 C", "1.1 C"])
def test_bad_manual_lines_are_visible_and_report_line_numbers(line):
    with pytest.raises(ValueError, match="Line 3:"):
        parse_manual("1 C\n\n" + line)


def test_manual_fractional_positions_round_trip_every_tick():
    events = tuple(ChordEvent(tick + 10000, tick + 1, tick, "F#m7(b5)/B") for tick in range(3840))
    restored = parse_manual(format_manual(events))
    assert [(event.bar, event.tick, event.symbol) for event in restored] == [
        (event.bar, event.tick, event.symbol) for event in events]
    assert format_manual((ChordEvent(0, 1, 240, "C"),)) == "1.1.2.0 C"


def test_manual_noncanonical_legacy_tick_normalizes_without_losing_time():
    events = parse_manual("1.1.1.500 C")
    assert events[0].tick == 500
    assert format_manual(events) == "1.1.3.20 C"


@pytest.mark.parametrize("text,expected", [
    ("c major 1 bars", "C"), ("c major 7 1 bars", "C7"), ("c major major 7 1 bars", "Cmaj7"),
    ("c minor 1 bars", "Cm"), ("c minor 7 1 bars", "Cm7"), ("b half diminished 1 bars", "Bm7(b5)"),
    ("c minor 7 flat 5 1 bars", "Cm7(b5)"), ("f sharp minor major 7 1 bars", "F♯m(maj7)"),
    ("d flat major major 9 1 bars", "D♭maj9"), ("c diminished 7 1 bars", "Cdim7"),
    ("c augmented 7 1 bars", "Caug7"), ("c suspended 2 1 bars", "Csus2"),
    ("c suspended 4 1 bars", "Csus4"), ("c 5 1 bars", "C5"), ("g 7 1 bars", "G 7"),
    ("g major/b flat 1 bars", "G/B♭"), ("No Chord 1 bars", "N.C."),
])
def test_logic_quality_mapping_matches_original(text, expected):
    assert parse_logic_text(text)[0].symbol == expected


def test_logic_snapshot_noise_export_and_fractional_positions():
    events = parse_logic_text("Accessibility window\nEXPORT|f sharp minor/a 3 bars 2 beats 3 divisions 17 ticks\nc major 1 bars")
    assert events == (ChordEvent(2, 1, 0, "C"), ChordEvent(1, 3, 1457, "F♯m/A"))
    assert parse_logic_text("unrelated window text") == ()


def test_logic_duplicates_and_out_of_meter_positions_fail():
    for text in ("c major 1 bars\ng major 1 bars", "c major 1 bars 4 beats", "c major 0 bars"):
        with pytest.raises(ValueError, match="Line"):
            parse_logic_text(text, meter=3)


def test_original_demo_is_embedded_exactly():
    document = demo_document()
    assert len(document.events) == 9
    assert document.events[3].symbol == "G 7"  # Deliberate original Logic spelling.
    assert document.events[5] == ChordEvent(5, 5, 1920, "Am")
    assert document.bars == 16
