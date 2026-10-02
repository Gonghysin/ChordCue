from dataclasses import replace
import json

import pytest

from chordcue.models import document_with_score
from chordcue.project import demo_document, load_project, save_project
from test_score_models import sample_score


def test_schema_two_roundtrip_all_score_fields_and_selection(tmp_path):
    path = tmp_path / "imported.chordcue.json"
    document = document_with_score(sample_score(), "p2")
    save_project(document, path)
    assert load_project(path) == document
    raw = json.loads(path.read_text("utf-8"))
    assert raw["schemaVersion"] == 2
    assert raw["score"] == document.score.to_dict()
    assert raw["selectedPartId"] == "p2"
    assert raw["events"] == []


def test_schema_one_remains_readable_and_saves_identically(tmp_path):
    path = tmp_path / "legacy.json"
    save_project(demo_document(), path)
    before = path.read_bytes()
    document = load_project(path)
    assert document.score is None and document.selected_part_id is None
    save_project(document, path)
    assert path.read_bytes() == before
    assert json.loads(before)["schemaVersion"] == 1


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("score"), lambda d: d.update(score=None), lambda d: d.pop("selectedPartId"),
    lambda d: d.update(selectedPartId="absent"), lambda d: d.update(selectedPartId=1),
    lambda d: d.update(bars=99), lambda d: d.update(schemaVersion=3),
    lambda d: d["score"].update(formatVersion=3), lambda d: d.update(schemaVersion=1),
])
def test_invalid_v2_load_never_changes_existing_user_document(tmp_path, mutate):
    old = demo_document()
    path = tmp_path / "score.json"
    save_project(document_with_score(sample_score()), path)
    raw = json.loads(path.read_text("utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw), "utf-8")
    with pytest.raises(ValueError):
        load_project(path)
    assert old == demo_document()


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_score_atomic_save_failure_preserves_complete_prior_project(tmp_path, monkeypatch, operation):
    path = tmp_path / "score.json"
    old = document_with_score(sample_score())
    save_project(old, path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("simulated score storage failure")

    monkeypatch.setattr("chordcue.project.os." + operation, fail)
    with pytest.raises(OSError):
        save_project(replace(old, selected_part_id="p2"), path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
    assert load_project(path) == old


def test_invalid_score_validation_finishes_before_atomic_save(tmp_path):
    path = tmp_path / "score.json"
    old = document_with_score(sample_score())
    save_project(old, path)
    before = path.read_bytes()
    object.__setattr__(old.score.tempo_changes[0], "bpm", float("nan"))
    with pytest.raises(ValueError):
        save_project(old, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_file_size_and_duplicate_score_keys_are_rejected(tmp_path, monkeypatch):
    path = tmp_path / "score.json"
    save_project(document_with_score(sample_score()), path)
    source = path.read_text("utf-8")
    path.write_text(source.replace('"formatVersion": 2', '"formatVersion": 3, "formatVersion": 2'), "utf-8")
    with pytest.raises(ValueError, match="duplicate JSON"):
        load_project(path)
    path.write_text(source, "utf-8")
    monkeypatch.setattr("chordcue.project.MAX_SCORE_BYTES", 80)
    with pytest.raises(ValueError, match="32 MiB"):
        load_project(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="32 MiB"):
        save_project(document_with_score(sample_score()), path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("source_name,fixture_name", [
    ("techniques.musicxml", "techniques-xml.score.json"),
    ("techniques.gp", "techniques-gp.score.json"),
])
def test_actual_import_json_atomic_save_and_reopen_keep_technique_semantics(tmp_path, source_name, fixture_name):
    from pathlib import Path
    import shutil
    import subprocess
    from chordcue.score_models import ScoreIR
    node = shutil.which("node")
    assert node
    root = Path(__file__).parents[2]
    source = root / "windows/tests/fixtures" / source_name
    module = "MusicXMLImport.js" if source_name.endswith("musicxml") else "GuitarProImport.js"
    script = "const fs=require('fs'),imp=require(process.argv[1]);process.stdout.write(JSON.stringify(imp.importScore(new Uint8Array(fs.readFileSync(process.argv[2])),process.argv[3])));"
    result = subprocess.run([node, "-e", script, str(root / "Resources/score" / module), str(source), source_name],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    raw = json.loads(result.stdout)
    expected = json.loads((source.parent / fixture_name).read_text("utf-8"))
    assert raw == expected
    score = ScoreIR.from_dict(raw)
    destination = tmp_path / "techniques.chordcue.json"
    document = document_with_score(score)
    save_project(document, destination)
    reopened = load_project(destination)
    assert reopened == document
    assert reopened.score.to_dict() == raw
    assert json.loads(destination.read_text("utf-8"))["schemaVersion"] == 2
    assert any(note.techniques for part in reopened.score.parts for staff in part.staves for event in staff.events for note in event.notes)


def test_save_as_does_not_overwrite_future_embedded_score_version(tmp_path):
    path = tmp_path / "future.json"
    document = document_with_score(sample_score())
    save_project(document, path)
    raw = json.loads(path.read_text("utf-8"))
    raw["score"]["formatVersion"] = 3
    path.write_text(json.dumps(raw), "utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="安全覆盖"):
        save_project(document, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_embedded_v1_score_migrates_on_open_and_saves_as_v2(tmp_path):
    path = tmp_path / "legacy-score.json"
    document = document_with_score(sample_score())
    save_project(document, path)
    raw = json.loads(path.read_text("utf-8"))
    raw["score"]["formatVersion"] = 1
    for part in raw["score"]["parts"]:
        for staff in part["staves"]:
            for event in staff["events"]:
                del event["techniques"]
                for note in event["notes"]:
                    del note["techniques"]
                    del note["writtenPitch"]
    path.write_text(json.dumps(raw), "utf-8")
    reopened = load_project(path)
    assert reopened.score.format_version == 2
    save_project(reopened, path)
    migrated = json.loads(path.read_text("utf-8"))
    assert migrated["schemaVersion"] == 2 and migrated["score"]["formatVersion"] == 2
