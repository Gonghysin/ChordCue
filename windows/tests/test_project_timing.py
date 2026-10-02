from dataclasses import replace
import json

import pytest

from chordcue.models import ChartDocument, ChordEvent, KeySection, LoopRange, MusicalKey, TimingChange
from chordcue.project import load_project, save_project
from chordcue.timing import apply_timing
from test_timing import source_document


def mapped_document():
    return apply_timing(ChartDocument(name="变拍练习", bars=3, events=(ChordEvent(84, 2, 2400, "C/G"),),
        forced_key=MusicalKey(2), manual_sections=(KeySection(3, MusicalKey(9, True)),),
        detect_changes=False, original_key=MusicalKey(0), loop=LoopRange(2, 4)),
        (TimingChange(1, 75.5, 4, 4), TimingChange(2, 99, 6, 8)))


def test_schema_three_round_trip_retains_manual_identity_and_every_legacy_field(tmp_path):
    document = mapped_document()
    path = tmp_path / "mapped.chordcue.json"
    save_project(document, path)
    assert load_project(path) == document
    raw = json.loads(path.read_text("utf-8"))
    assert raw["schemaVersion"] == 3
    assert raw["timingChanges"] == [{"bar": 1, "bpm": 75.5, "numerator": 4, "denominator": 4},
                                    {"bar": 2, "bpm": 99, "numerator": 6, "denominator": 8}]
    assert "score" not in raw and "selectedPartId" not in raw
    before = path.read_bytes()
    save_project(load_project(path), path)
    assert path.read_bytes() == before


def test_source_timing_edits_remain_schema_two_not_a_parallel_table(tmp_path):
    document = apply_timing(source_document(), (TimingChange(1, 75, 6, 8),))
    path = tmp_path / "source.json"
    save_project(document, path)
    assert load_project(path) == document
    raw = json.loads(path.read_text("utf-8"))
    assert raw["schemaVersion"] == 2 and "timingChanges" not in raw


@pytest.mark.parametrize("mutate", [
    lambda data: data.pop("timingChanges"), lambda data: data.update(timingChanges=[]),
    lambda data: data.update(timingChanges={}), lambda data: data.update(schemaVersion=4),
    lambda data: data.update(schemaVersion=1), lambda data: data.update(score=None),
    lambda data: data.update(selectedPartId=None),
    lambda data: data["timingChanges"][0].update(bar=2),
    lambda data: data["timingChanges"][0].update(bpm=True),
    lambda data: data["timingChanges"][0].update(denominator=3),
    lambda data: data["timingChanges"][0].update(beatUnit="quarter"),
    lambda data: data["timingChanges"].append(dict(data["timingChanges"][0])),
    lambda data: data["timingChanges"][1].update(numerator=5),
])
def test_invalid_schema_three_is_rejected_wholly(tmp_path, mutate):
    path = tmp_path / "invalid.json"
    original = mapped_document()
    save_project(original, path)
    data = json.loads(path.read_text("utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(ValueError):
        load_project(path)
    assert original == mapped_document()


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_mapped_atomic_failure_preserves_original_project_and_cleans_temp(tmp_path, monkeypatch, failure):
    path = tmp_path / "original.json"
    original = ChartDocument(bars=3)
    save_project(original, path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("storage failed")

    monkeypatch.setattr("chordcue.project.os." + failure, fail)
    with pytest.raises(OSError, match="storage failed"):
        save_project(mapped_document(), path)
    assert path.read_bytes() == before and load_project(path) == original
    assert list(tmp_path.iterdir()) == [path]


def test_future_schema_not_destroyed_and_removing_table_restores_schema_one(tmp_path):
    path = tmp_path / "future.json"
    path.write_text('{"schemaVersion":4}', "utf-8")
    with pytest.raises(ValueError, match="安全覆盖"):
        save_project(mapped_document(), path)
    assert path.read_text("utf-8") == '{"schemaVersion":4}'
    path = tmp_path / "mapped.json"
    save_project(mapped_document(), path)
    # Removing a map explicitly retains chord positions; the fixed meter must fit.
    document = replace(mapped_document(), timing_changes=(), meter=4)
    save_project(document, path)
    assert load_project(path) == document
    assert json.loads(path.read_text("utf-8"))["schemaVersion"] == 1
