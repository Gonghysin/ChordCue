from dataclasses import replace
import json

import pytest

from chordcue.models import ChartDocument, ChordEvent, KeySection, LoopRange, MusicalKey
from chordcue.project import demo_document, load_project, save_project


def test_round_trip_every_project_field_and_stable_event_ids(tmp_path):
    document = ChartDocument(name="原创练习 ♭", bars=8, bpm=87.25, meter=7,
                             events=(ChordEvent(812, 3, 6441, "D♭maj7/F"), ChordEvent(4, 1, 0, "N.C.")),
                             forced_key=MusicalKey(1), manual_sections=(KeySection(5, MusicalKey(10, True)),),
                             detect_changes=False, original_key=MusicalKey(9, True), loop=LoopRange(2, 9))
    path = tmp_path / "chart.chordcue.json"
    save_project(document, path)
    assert load_project(path) == document
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schemaVersion"] == 1
    assert raw["events"][0]["id"] == 812
    assert raw["events"][0]["tick"] == 6441
    assert set(raw) == {"schemaVersion", "name", "bars", "bpm", "meter", "events", "forcedKey",
                        "manualSections", "detectChanges", "originalKey", "loop"}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.fixture
def project_data(tmp_path):
    path = tmp_path / "demo.json"
    save_project(demo_document(), path)
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("field,value", [("schemaVersion", 2), ("schemaVersion", True), ("schemaVersion", 1.0),
                                        ("bars", True), ("bpm", float("nan")), ("meter", 13),
                                        ("events", {}), ("manualSections", {}), ("detectChanges", "true"),
                                        ("forcedKey", {"root": 12, "minor": False}),
                                        ("originalKey", {"root": 1, "minor": 0}),
                                        ("loop", {"startBar": 1, "endBarExclusive": 18})])
def test_invalid_json_documents_rejected_completely(project_data, field, value):
    path, data = project_data
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(path)


def test_duplicate_onsets_ids_and_missing_required_fields(project_data):
    path, source = project_data
    for mutate in (
        lambda data: data["events"].append(dict(data["events"][0])),
        lambda data: data["events"][0].update({"tick": 3840}),
        lambda data: data["events"][0].update({"bar": 17}),
        lambda data: data["manualSections"].append({"bar": 1, "key": None}),
        lambda data: data.pop("name"),
    ):
        data = json.loads(json.dumps(source))
        mutate(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            load_project(path)


def test_json_duplicate_keys_rejected_even_if_last_value_is_valid(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schemaVersion":2,"schemaVersion":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON field"):
        load_project(path)


@pytest.mark.parametrize("failing_operation", ["fsync", "replace"])
def test_save_failure_preserves_old_file_and_cleans_own_temp(tmp_path, monkeypatch, failing_operation):
    path = tmp_path / "chart.json"
    old = demo_document()
    save_project(old, path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("simulated storage failure")

    monkeypatch.setattr("chordcue.project.os." + failing_operation, fail)
    with pytest.raises(OSError, match="simulated"):
        save_project(replace(old, name="Changed"), path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
    assert load_project(path) == old


def test_validation_finishes_before_touching_existing_file(tmp_path):
    path = tmp_path / "chart.json"
    document = demo_document()
    save_project(document, path)
    before = path.read_bytes()
    # Defeat frozen construction deliberately to exercise the persistence trust boundary.
    object.__setattr__(document, "bpm", float("inf"))
    with pytest.raises(ValueError):
        save_project(document, path)
    assert path.read_bytes() == before


def test_utf8_bom_is_accepted(project_data):
    path, data = project_data
    path.write_text(json.dumps(data), encoding="utf-8-sig")
    assert load_project(path) == demo_document()


@pytest.mark.parametrize("content", ['{"schemaVersion":2}', '{broken json'])
def test_save_as_refuses_unknown_or_damaged_existing_project(tmp_path, content):
    path = tmp_path / "future.chordcue.json"
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="另存为"):
        save_project(demo_document(), path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
