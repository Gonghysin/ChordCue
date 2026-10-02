"""Versioned project JSON with complete validation and atomic replacement."""

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .models import ChartDocument, ChordEvent, KeySection, LoopRange, MusicalKey
from .parsing import parse_logic_text

SCHEMA_VERSION = 1

# Original Resources/DemoChords.txt, embedded so the demo also works without a
# resource locator and in pure-core tests. Keep this in sync with that asset.
_DEMO = """c major 1 bars
a minor 2 bars
f major 3 bars
g 7 4 bars
c major 5 bars 1 beats
a minor 5 bars 3 beats
d minor 7 6 bars
g 7 7 bars
c major 8 bars
"""


def _key_data(key: MusicalKey | None) -> dict[str, Any] | None:
    return None if key is None else {"root": key.root, "minor": key.is_minor}


def _object(data: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be an object")
    missing = fields.difference(data)
    if missing:
        raise ValueError(f"{name} missing fields: {', '.join(sorted(missing))}")
    return data


def _key(data: Any) -> MusicalKey | None:
    if data is None:
        return None
    item = _object(data, {"root", "minor"}, "key")
    return MusicalKey(item["root"], item["minor"])


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def load_project(path: str | os.PathLike[str]) -> ChartDocument:
    """Return a wholly validated document; never mutate an existing project."""
    with Path(path).open("r", encoding="utf-8-sig") as source:
        data = json.load(source, object_pairs_hook=_no_duplicate_keys, parse_constant=_invalid_constant)
    data = _object(data, {"schemaVersion", "name", "bars", "bpm", "meter", "events", "forcedKey",
                          "manualSections", "detectChanges", "originalKey", "loop"}, "project")
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported project schemaVersion: {data['schemaVersion']!r}")
    if not isinstance(data["events"], list) or not isinstance(data["manualSections"], list):
        raise ValueError("events and manualSections must be arrays")
    events = []
    for raw in data["events"]:
        item = _object(raw, {"id", "bar", "tick", "symbol"}, "event")
        events.append(ChordEvent(item["id"], item["bar"], item["tick"], item["symbol"]))
    sections = []
    for raw in data["manualSections"]:
        item = _object(raw, {"bar", "key"}, "section")
        key = _key(item["key"])
        if key is None:
            raise ValueError("section key cannot be null")
        sections.append(KeySection(item["bar"], key))
    loop = None
    if data["loop"] is not None:
        item = _object(data["loop"], {"startBar", "endBarExclusive"}, "loop")
        loop = LoopRange(item["startBar"], item["endBarExclusive"])
    return ChartDocument(name=data["name"], bars=data["bars"], bpm=data["bpm"], meter=data["meter"],
                         events=tuple(events), forced_key=_key(data["forcedKey"]),
                         manual_sections=tuple(sections), detect_changes=data["detectChanges"],
                         original_key=_key(data["originalKey"]), loop=loop)


def save_project(document: ChartDocument, path: str | os.PathLike[str]) -> None:
    """Write a flushed temporary sibling, then atomically replace the target.

    Validation/serialization and all writing finish before replacing an existing
    file. Failed writes remove only their own temporary sibling.
    """
    document.validate()
    data = {
        "schemaVersion": SCHEMA_VERSION,
        "name": document.name, "bars": document.bars, "bpm": document.bpm, "meter": document.meter,
        "events": [{"id": event.id, "bar": event.bar, "tick": event.tick, "symbol": event.symbol}
                   for event in document.events],
        "forcedKey": _key_data(document.forced_key),
        "manualSections": [{"bar": section.first_bar, "key": _key_data(section.key)}
                           for section in document.manual_sections],
        "detectChanges": document.detect_changes, "originalKey": _key_data(document.original_key),
        "loop": None if document.loop is None else {"startBar": document.loop.start_bar,
                                                     "endBarExclusive": document.loop.end_bar_exclusive},
    }
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    destination = Path(path).absolute()
    # Save As can target a file we never opened. Never destroy an unsupported
    # future project (or a damaged document) merely because its name was chosen.
    if destination.exists():
        try:
            load_project(destination)
        except (ValueError, UnicodeError) as error:
            raise ValueError("目标文件无法安全覆盖，请另存为新文件：" + str(error)) from error
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=f".{destination.name}.", suffix=".tmp",
                                         dir=destination.parent, delete=False) as target:
            temporary = Path(target.name)
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def demo_document() -> ChartDocument:
    return ChartDocument(name="ChordCue 演示", events=parse_logic_text(_DEMO))
