"""Real file import -> ScoreIR -> clock playback, without manual timing edits.

Original checked-in fixtures supply all headers, tracks and music. Tiny in-memory
variants add one legacy bar or change a GP container version; no importer/model
is mocked, and no generated binary fixture is added to the repository.
"""

from fractions import Fraction
import base64
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

import pytest

from chordcue.models import document_with_score
from chordcue.score_models import QuarterFraction as Q, ScoreIR
from chordcue.transport import StandaloneTransport
from test_transport import FakeClock

ROOT = Path(__file__).parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
NODE = shutil.which("node")
MODERN_CASES = (
    ("issue1-original.gp", "gp", "GP8"),
    ("issue1-original.gp", "gp7", "GP7"),
    ("issue1-original.gp", "gp6", "GP6"),
    ("issue1-original.gp", "gp6-compressed", "GP6"),
    ("issue1-original.musicxml", "xml", "MusicXML"),
    ("issue1-original.gp", "gp-dotted", "GP8"),
)
LEGACY_CASES = (
    ("issue1-original-legacy.gp3", "GP3"), ("issue1-original-legacy.gp4", "GP4"),
    ("issue1-original-legacy.gp5", "GP5"), ("issue1-original-legacy.gpx", "GP6"),
    ("issue1-original-legacy-compressed.gpx", "GP6"),
)
IMPORTED: dict[tuple[str, str], tuple[str, ScoreIR, bytes]] = {}

IMPORT = r"""
const fs=require('node:fs'),path=require('node:path'),root=process.argv[1];
const GP=require(path.join(root,'Resources/score/GuitarProImport.js'));
const XML=require(path.join(root,'Resources/score/MusicXMLImport.js'));
const IO=require(path.join(root,'Resources/score/ScoreIO.js'));
function importOne({mode,name,data}){
let bytes=new Uint8Array(Buffer.from(data,'base64'));
if(['gp7','gp6','gp6-compressed'].includes(mode)){
 const entries=IO.zipEntries(bytes),version=mode==='gp7'?'7.0':'6.0';
 const gpif=new TextEncoder().encode(IO.decode(IO.unpack(entries.get('Content/score.gpif')))
   .replace(/<GPVersion>[^<]*<\/GPVersion>/,'<GPVersion>'+version+'</GPVersion>'));
 if(mode==='gp7'){
  const zip=require(path.join(root,'Resources/vendor/fflate/umd/index.js')),members={};
  for(const [name,entry] of entries)members[name]=name==='Content/score.gpif'?gpif:IO.unpack(entry);
  bytes=zip.zipSync(members);
 }else{
  const legacy=require(path.join(root,'tools/generate_legacy_gp_fixtures.cjs'));
  bytes=legacy.bcfs(gpif);if(mode==='gp6-compressed')bytes=legacy.bcfz(bytes);
 }
}
if(['gp-middle','gp-pickup','gp-dotted'].includes(mode)){
 const alpha=require(path.join(root,'Resources/vendor/alphatab/dist/alphaTab.js'));
 if(mode==='gp-dotted'){
  const entries=IO.zipEntries(bytes),zip=require(path.join(root,'Resources/vendor/fflate/umd/index.js')),members={};
  const original=IO.decode(IO.unpack(entries.get('Content/score.gpif')));
  const changed=original.replace('<Value>135 2</Value>','<Value>90 3</Value>');
  if(changed===original)throw Error('Expected original tempo reference not found');
  for(const [name,entry] of entries)members[name]=name==='Content/score.gpif'?new TextEncoder().encode(changed):IO.unpack(entry);
  bytes=zip.zipSync(members);
 }else{
  const model=alpha.importer.ScoreLoader.loadScoreFromBytes(bytes);
  if(mode==='gp-middle')model.masterBars[0].tempoAutomations.push(alpha.model.Automation.buildTempoAutomation(false,.5,60,2));
  else{
   model.masterBars[0].isAnacrusis=true;
   for(const track of model.tracks)for(const staff of track.staves)for(const voice of staff.bars[0].voices){
    voice.beats=voice.beats.slice(0,1);voice.beats[0].duration=alpha.model.Duration.Eighth;voice.beats[0].dots=0;
   }
  }
  bytes=new alpha.exporter.Gp7Exporter().export(model);
 }
}
const isXML=mode==='xml';
const score=(isXML?XML:GP).importScore(bytes,name);
return {name,mode,version:isXML?'MusicXML':GP.identify(bytes),score};
}
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(cases.map(item=>{
 try{return importOne(item);}catch(error){throw Error(item.name+' / '+item.mode+': '+error.message);}
})));
"""


def import_file(data: bytes, name: str, mode: str = "gp") -> tuple[str, ScoreIR]:
    version, score, source = IMPORTED[name, mode]
    assert source == data
    return version, score


def engine_for(score: ScoreIR):
    clock = FakeClock()
    document = document_with_score(score)
    assert document.score is score and document.timing_changes == ()
    engine = StandaloneTransport(document, clock)
    engine.play()
    clock.advance(400_000_000)
    return engine, clock


@pytest.mark.parametrize("name,mode,version", MODERN_CASES)
def test_real_imports_follow_source_tempo_meter_and_endings_without_manual_table(name, mode, version):
    identified, score = import_file((FIXTURES / name).read_bytes(), name, mode)
    assert identified == version
    assert [(change.measure_id, change.offset, change.bpm) for change in score.tempo_changes] == [
        ("m1", Q(0), 120), ("m3", Q(0), 135)]
    engine, clock = engine_for(score)
    assert [item.id for item in engine.plan.occurrences] == ["m1@1", "m2@1", "m1@2", "m3@1"]
    epoch = engine.snapshot()["discontinuity"]
    for advance, occurrence, bpm, meter, quarter in [
        (0, "m1@1", 120, "4/4", 0), (2, "m2@1", 120, "4/4", 4),
        (2, "m1@2", 120, "4/4", 8), (2, "m3@1", 135, "6/8", 12),
    ]:
        clock.advance(advance * 1_000_000_000)
        sample = engine.snapshot(include_route=False)
        assert (sample["occurrenceId"], sample["bpm"], sample["meter"], sample["playQuarter"]) == (
            occurrence, bpm, meter, quarter)
        assert sample["sourceOffsetQuarter"] == 0 and sample["discontinuity"] == epoch
    assert score.measures[2].duration == Q(3)
    assert engine.plan.duration_seconds == pytest.approx(float(Fraction(22, 3)))
    assert engine.document.score is score and engine.document.timing_changes == ()


@pytest.mark.parametrize("name,version", LEGACY_CASES)
def test_checked_in_legacy_files_use_imported_bpm_and_repeat_without_manual_table(name, version):
    identified, score = import_file((FIXTURES / name).read_bytes(), name)
    assert identified == version
    engine, _ = engine_for(score)
    assert engine.snapshot()["bpm"] == 108 and engine.snapshot()["meter"] == "4/4"
    assert [item.id for item in engine.plan.occurrences] == ["m1@1", "m1@2"]
    engine.seek(1, occurrence=2)
    assert engine.snapshot()["occurrenceId"] == "m1@2"
    assert engine.plan.duration_seconds == pytest.approx(float(Fraction(40, 9)))


def legacy_change_bytes(version: int) -> bytes:
    """Reuse the existing legacy header/track/music, append a dotted-half bar.

    GP3/4/5 store the new meter in their master-bar header and quarter BPM in a
    beat mix-table change. This tiny addition exercises both real binary fields.
    """
    original = (FIXTURES / f"issue1-original-legacy.gp{version}").read_bytes()
    repeat = 2 if version == 5 else 1
    marker = struct.pack("<ii", 1, 1) + bytes([0x4F, 4, 4, repeat, 0, 0])
    assert original.count(marker) == 1
    count_at = original.index(marker)
    track_at = count_at + 8 + (13 if version == 5 else 6)
    body_at = track_at + (143 if version == 5 else 98)
    assert original[body_at:body_at + (5 if version == 5 else 4)] == (
        (b"\0" if version == 5 else b"") + struct.pack("<i", 3))
    padding = bytes(7) if version == 5 else b""
    first_header = bytes([0x47, 4, 4, 0, 0]) + padding
    second_header = bytes([0x0B, 6, 8, repeat]) + padding
    # Instrument unchanged, six mixer controls unchanged, instantaneous tempo 135.
    mix = b"\xff" + (bytes(16) if version == 5 else b"") + b"\xff" * 6
    if version == 5:
        mix += struct.pack("<i", 1) + b"\0"  # empty tempo label
    mix += struct.pack("<i", 135) + b"\0"
    if version >= 4:
        mix += b"\0"  # mixer flags
    if version == 5:
        mix += b"\xff"  # no wah
    second_body = (b"\0" if version == 5 else b"") + struct.pack("<i", 1)
    second_body += bytes([0x11, 0xFF]) + mix + bytes([64, 32, 1, 0])  # dotted half, E4
    if version == 5:
        second_body += bytes(3) + struct.pack("<i", 0)  # note/beat flags, empty second voice
    return (original[:count_at] + struct.pack("<ii", 2, 1) + first_header + second_header
            + original[track_at:body_at] + original[body_at:] + second_body)


@pytest.fixture(scope="module", autouse=True)
def real_imports():
    """Use one real Node process for all files; each remains independently parsed."""
    assert NODE, "Node is required to exercise the real packaged importers"
    cases = [(name, mode, (FIXTURES / name).read_bytes()) for name, mode, _ in MODERN_CASES]
    cases.extend((name, "gp", (FIXTURES / name).read_bytes()) for name, _ in LEGACY_CASES)
    cases.extend((f"timing.gp{version}", "gp", legacy_change_bytes(version)) for version in (3, 4, 5))
    original = (FIXTURES / "issue1-original.gp").read_bytes()
    cases.extend((("middle.gp", "gp-middle", original), ("pickup.gp", "gp-pickup", original),
                  ("pickup-middle.musicxml", "xml", XML_EDGE)))
    requests = [{"name": name, "mode": mode, "data": base64.b64encode(data).decode("ascii")}
                for name, mode, data in cases]
    # File IO avoids Windows subprocess pipe threads. Batching also keeps this
    # suite's process startup cost independent of the number of test cases.
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        source.write(json.dumps(requests).encode("utf-8"))
        source.seek(0)
        result = subprocess.run([NODE, "-e", IMPORT, str(ROOT)], stdin=source, stdout=output,
                                stderr=errors, timeout=20)
        errors.seek(0)
        assert result.returncode == 0, errors.read().decode("utf-8", errors="replace")
        output.seek(0)
        imported = json.load(output)
    assert len(imported) == len(cases)
    inputs = {(name, mode): data for name, mode, data in cases}
    for item in imported:
        key = item["name"], item["mode"]
        IMPORTED[key] = item["version"], ScoreIR.from_dict(item["score"]), inputs[key]
    yield
    IMPORTED.clear()


@pytest.mark.parametrize("version", [3, 4, 5])
def test_legacy_binary_tempo_meter_changes_reapply_on_repeat(version):
    identified, score = import_file(legacy_change_bytes(version), f"timing.gp{version}")
    assert identified == f"GP{version}"
    assert [(m.meter.numerator, m.meter.denominator) for m in score.measures] == [(4, 4), (6, 8)]
    assert [(change.measure_id, change.offset, change.bpm) for change in score.tempo_changes] == [
        ("m1", Q(0), 108), ("m2", Q(0), 135)]
    engine, clock = engine_for(score)
    assert [item.id for item in engine.plan.occurrences] == ["m1@1", "m2@1", "m1@2", "m2@2"]
    epoch = engine.snapshot()["discontinuity"]
    # Ceil to the first real nanosecond after these rational clock boundaries.
    for ns, occurrence, bpm, meter in [(2_222_222_223, "m2@1", 135, "6/8"),
                                      (3_555_555_556, "m1@2", 108, "4/4"),
                                      (5_777_777_778, "m2@2", 135, "6/8")]:
        clock.ns = 12_345_678_000_000 + 400_000_000 + ns
        sample = engine.snapshot(include_route=False)
        assert (sample["occurrenceId"], sample["bpm"], sample["meter"]) == (occurrence, bpm, meter)
        assert sample["sourceOffsetQuarter"] < 1e-8 and sample["discontinuity"] == epoch
    assert engine.plan.duration_seconds == pytest.approx(float(Fraction(64, 9)))


def test_gp_midbar_tempo_and_repeat_restore_source_start_tempo():
    _, score = import_file((FIXTURES / "issue1-original.gp").read_bytes(), "middle.gp", "gp-middle")
    assert score.tempo_changes[1].offset == Q(2) and score.tempo_changes[1].bpm == 60
    engine, clock = engine_for(score)
    for advance, occurrence, offset, bpm in [
        (1, "m1@1", 2, 60), (2, "m2@1", 0, 60), (4, "m1@2", 0, 120),
        (1, "m1@2", 2, 60), (2, "m3@1", 0, 135),
    ]:
        clock.advance(advance * 1_000_000_000)
        sample = engine.snapshot(include_route=False)
        assert (sample["occurrenceId"], sample["sourceOffsetQuarter"], sample["bpm"]) == (
            occurrence, offset, bpm)


def test_gp_pickup_uses_actual_half_quarter_duration_not_nominal_four_quarters():
    _, score = import_file((FIXTURES / "issue1-original.gp").read_bytes(), "pickup.gp", "gp-pickup")
    assert score.measures[0].duration == Q(1, 2)
    assert score.measures[0].meter.quarters == 4
    engine, clock = engine_for(score)
    clock.advance(250_000_000)
    assert engine.snapshot()["occurrenceId"] == "m2@1"
    clock.advance(2_000_000_000)
    assert engine.snapshot()["occurrenceId"] == "m1@2"
    clock.advance(250_000_000)
    assert engine.snapshot()["meter"] == "6/8" and engine.snapshot()["bpm"] == 135
    assert engine.plan.duration_seconds == pytest.approx(float(Fraction(23, 6)))


XML_EDGE = b'''<score-partwise version="4.0"><part-list><score-part id="p"><part-name>P</part-name></score-part></part-list>
    <part id="p"><measure number="0" implicit="yes"><attributes><divisions>2</divisions><time><beats>3</beats><beat-type>4</beat-type></time></attributes>
    <direction><sound tempo="120"/></direction><note><rest/><duration>1</duration></note></measure>
    <measure number="A"><attributes><time><beats>6</beats><beat-type>8</beat-type></time></attributes><barline location="left"><repeat direction="forward"/></barline>
    <direction><direction-type><metronome><beat-unit>quarter</beat-unit><beat-unit-dot/><per-minute>60</per-minute></metronome></direction-type></direction>
    <note><rest/><duration>2</duration></note><direction><sound tempo="180"/></direction><note><rest/><duration>4</duration></note>
    <barline location="right"><repeat direction="backward" times="2"/></barline></measure>
    <measure number="B"><attributes><time><beats>5</beats><beat-type>8</beat-type></time></attributes>
    <direction><direction-type><metronome><beat-unit>eighth</beat-unit><per-minute>90</per-minute></metronome></direction-type></direction>
    <note><rest/><duration>5</duration></note></measure></part></score-partwise>'''


def test_musicxml_pickup_midbar_tempo_and_note_unit_conversion_repeat_automatically():
    # One half-quarter pickup; then 6/8 dotted-quarter 60 -> quarter 90,
    # quarter 180 after one quarter; finally 5/8 eighth 90 -> quarter 45.
    _, score = import_file(XML_EDGE, "pickup-middle.musicxml", "xml")
    assert [m.duration.as_fraction() for m in score.measures] == [Fraction(1, 2), 3, Fraction(5, 2)]
    assert [(change.measure_id, change.offset, change.bpm) for change in score.tempo_changes] == [
        ("m1", Q(0), 120), ("m2", Q(0), 90), ("m2", Q(1), 180), ("m3", Q(0), 45)]
    engine, clock = engine_for(score)
    clock.advance(250_000_000)
    assert engine.snapshot()["bar"] == 2 and engine.snapshot()["bpm"] == 90
    clock.advance(666_666_667)
    assert engine.snapshot()["bpm"] == 180
    engine.seek(2, occurrence=2)
    assert engine.snapshot()["occurrenceId"] == "m2@2" and engine.snapshot()["bpm"] == 90
    engine.seek(3)
    assert engine.snapshot()["bpm"] == 45 and engine.snapshot()["meter"] == "5/8"
    assert engine.plan.duration_seconds == pytest.approx(float(Fraction(25, 4)))
