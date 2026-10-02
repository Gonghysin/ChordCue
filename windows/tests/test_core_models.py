from dataclasses import FrozenInstanceError, replace

import pytest

from chordcue.models import ChartDocument, ChordEvent, KeySection, LoopRange, MusicalKey, PPQ, event_position


def test_document_is_deeply_immutable_and_replace_validates():
    document = ChartDocument(events=(ChordEvent(41, 2, 1200, "F♯m7/A"),))
    with pytest.raises(FrozenInstanceError):
        document.bars = 2
    with pytest.raises(FrozenInstanceError):
        document.events[0].symbol = "C"
    with pytest.raises(ValueError):
        replace(document, bars=1)
    assert replace(document, bpm=90).events[0].id == 41


@pytest.mark.parametrize("changes", [
    {"bars": True}, {"bars": 0}, {"bars": 1.0}, {"meter": True}, {"meter": 0}, {"meter": 13},
    {"bpm": True}, {"bpm": float("nan")}, {"bpm": float("inf")}, {"bpm": -float("inf")},
    {"bpm": 19.9}, {"bpm": 300.01}, {"bpm": "120"}, {"bpm": 10 ** 400},
    {"events": []}, {"manual_sections": []}, {"events": ("C",)}, {"manual_sections": ("C",)},
    {"detect_changes": 1}, {"forced_key": 0}, {"original_key": "C"}, {"loop": (1, 2)},
    {"name": ""}, {"name": "\n"}, {"name": 3},
])
def test_invalid_document_values(changes):
    with pytest.raises(ValueError):
        ChartDocument(**changes)


@pytest.mark.parametrize("bpm", [20, 20.5, 299.99, 300])
def test_bpm_endpoints(bpm):
    ChartDocument(bpm=bpm).validate()


@pytest.mark.parametrize("meter", range(1, 13))
def test_all_supported_quarter_note_meters(meter):
    ChartDocument(meter=meter, events=(ChordEvent(0, 1, meter * PPQ - 1, "C"),)).validate()
    with pytest.raises(ValueError):
        ChartDocument(meter=meter, events=(ChordEvent(0, 1, meter * PPQ, "C"),))


@pytest.mark.parametrize("arguments", [(True, 1, 0, "C"), (0, True, 0, "C"), (0, 1, True, "C"),
                                         (-1, 1, 0, "C"), (0, 0, 0, "C"), (0, 1, -1, "C"),
                                         (0, 1, 0, ""), (0, 1, 0, "C\nF"), (0, 1, 0, None)])
def test_invalid_events(arguments):
    with pytest.raises(ValueError):
        ChordEvent(*arguments)


def test_duplicate_ids_and_onsets_rejected_independently():
    for events in ((ChordEvent(1, 1, 0, "C"), ChordEvent(1, 2, 0, "D")),
                   (ChordEvent(1, 1, 0, "C"), ChordEvent(2, 1, 0, "D"))):
        with pytest.raises(ValueError, match="duplicate"):
            ChartDocument(events=events)


@pytest.mark.parametrize("root,minor", [(-1, False), (12, False), (True, False), (1.0, False), (0, 1)])
def test_key_validation(root, minor):
    with pytest.raises(ValueError):
        MusicalKey(root, minor)


def test_relative_minor_labels():
    key = MusicalKey(9, True)
    assert key.major_family_root == 0
    assert key.label == "1=C · 6=A（小调）"
    assert key.selection_label == "A 小调"


def test_sections_and_loops_must_fit_project():
    with pytest.raises(ValueError):
        ChartDocument(bars=4, manual_sections=(KeySection(5, MusicalKey(2)),))
    with pytest.raises(ValueError, match="duplicate"):
        ChartDocument(manual_sections=(KeySection(3, MusicalKey(2)), KeySection(3, MusicalKey(5))))
    with pytest.raises(ValueError):
        ChartDocument(bars=4, loop=LoopRange(1, 6))
    for start, end in ((0, 2), (2, 2), (3, 2), (True, 2), (1, True)):
        with pytest.raises(ValueError):
            LoopRange(start, end)
    ChartDocument(bars=4, loop=LoopRange(1, 5)).validate()


@pytest.mark.parametrize("tick,expected", [(0, (1, 1, 0)), (239, (1, 1, 239)), (240, (1, 2, 0)),
                                           (959, (1, 4, 239)), (960, (2, 1, 0)), (3839, (4, 4, 239))])
def test_canonical_legacy_positions(tick, expected):
    position = event_position(ChordEvent(10, 3, tick, "C"))
    assert position == dict(zip(("bar", "beat", "division", "tick"), (3, *expected)))
