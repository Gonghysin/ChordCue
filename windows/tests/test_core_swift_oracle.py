"""Run differential checks against the real Swift implementation on macOS CI.

This is deliberately skipped when no oracle is configured. A Python-only run
does not establish Swift parity. Configured oracle failures are test failures.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from chordcue.models import ChartDocument, KeySection, MusicalKey, event_position
from chordcue.parsing import parse_logic_text
from chordcue.theory import analyze_sections, display_chord, note_name, number, parse_key, transpose

FIXTURE = Path(__file__).parent / "fixtures" / "theory_cases.json"


def _key(data):
    return None if data is None else MusicalKey(data["root"], data["minor"])


def _key_json(key):
    return None if key is None else {"root": key.root, "minor": key.is_minor, "family": key.major_family_root,
                                    "label": key.label, "selectionLabel": key.selection_label}


def _sections_json(sections):
    return [{"bar": section.first_bar, "key": _key_json(section.key)} for section in sections]


def _python_result(fixture):
    result = {
        "noteNames": [note_name(item["pitch"], item["preferFlats"]) for item in fixture["noteNames"]],
        "transposes": [transpose(item["symbol"], item["semitones"], item["preferFlats"])
                       for item in fixture["transposes"]],
        "displays": [display_chord(symbol) for symbol in fixture["displays"]],
        "numbers": [number(item["symbol"], _key(item["key"])) for item in fixture["numbers"]],
        "keys": [_key_json(parse_key(text)) for text in fixture["keys"]],
        "snapshots": [],
    }
    for item in fixture["snapshots"]:
        events = parse_logic_text(item["text"], meter=item["meter"])
        manual = tuple(KeySection(section["bar"], _key(section["key"])) for section in item["manualSections"])
        bars = max([16, *(event.bar for event in events), *(section.first_bar for section in manual)])
        document = ChartDocument(events=events, bars=bars, meter=item["meter"], forced_key=_key(item["forcedKey"]))
        sections = analyze_sections(document)
        result["snapshots"].append({
            "name": item["name"],
            "chords": [{"id": event.id, **event_position(event), "symbol": event.symbol} for event in events],
            "global": _sections_json(analyze_sections(replace(document, detect_changes=False))),
            "sections": _sections_json(sections),
            "manual": _sections_json(analyze_sections(replace(document, manual_sections=manual))),
            "degrees": [number(event.symbol, next(section.key for section in reversed(sections)
                                                  if section.first_bar <= event.bar)) for event in events],
        })
    return result


@pytest.fixture(scope="module")
def oracle_results():
    executable = os.environ.get("CHORDCUE_SWIFT_ORACLE")
    if not executable:
        pytest.skip("CHORDCUE_SWIFT_ORACLE not set; actual Swift parity requires macOS CI")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    completed = subprocess.run([executable, str(FIXTURE)], check=True, capture_output=True,
                               text=True, encoding="utf-8", timeout=90)
    return _python_result(fixture), json.loads(completed.stdout)


@pytest.mark.parametrize("category", ["noteNames", "transposes", "displays", "numbers", "keys", "snapshots"])
def test_against_actual_swift_source(oracle_results, category):
    python, swift = oracle_results
    assert python[category] == swift[category]


def test_oracle_fixture_is_valid_and_covers_modulation_without_requiring_swift():
    """Exercise the fixture/harness even in Windows runs; this is not a parity check."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = _python_result(fixture)
    modulation = next(item for item in result["snapshots"] if item["name"] == "c_to_d")
    assert [section["bar"] for section in modulation["sections"]] == [1, 9]
    assert len(result["transposes"]) >= 100
    assert len(result["numbers"]) >= 100
