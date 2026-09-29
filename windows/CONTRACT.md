# Windows implementation contract

Feature issue: https://github.com/Gonghysin/ChordCue/issues/2
Baseline: 726df0b3a0e0b1d750aa009aaa753ab424226515. Branch: codex/windows-desktop.
Python 3.13, src layout `windows/src/chordcue`, tests `windows/tests`.
Root owns integration and dependency files; each agent owns only assigned modules.

## Domain (A1)

`models.py`: frozen dataclasses and `PPQ=960`.
- `MusicalKey(root: int, is_minor: bool=False)`: `major_family_root`, `label`, `selection_label` properties.
- `KeySection(first_bar: int, key: MusicalKey)`.
- `ChordEvent(id: int, bar: int, tick: int, symbol: str)`: tick is zero-based within bar, not the legacy tick component.
- `LoopRange(start_bar: int, end_bar_exclusive: int)`.
- `ChartDocument(name='未命名', bars=16, bpm=120.0, meter=4, events=(), forced_key=None, manual_sections=(), detect_changes=True, original_key=None, loop=None)`.
  `events` and `manual_sections` are tuples. `validate()` raises ValueError, rejects bool where integer expected, nonfinite numbers, unsupported meters, out-of-range events/keys/loops and duplicate event ids/onsets. All callers use dataclasses.replace for updates. Meter is numerator, denominator is always 4.
- `event_position(event)` converts to legacy `{bar,beat,division,tick}` with canonical tick remainder 0..239; absolute position is `(bar-1)*meter + event.tick/960`.

`parsing.py`: `parse_manual(text: str, meter: int=4) -> tuple[ChordEvent,...]`; `format_manual(events) -> str`; `parse_logic_text(text, meter=4) -> tuple[...]` for existing original demo/golden fixtures. Raise ValueError with line numbers on malformed manual input. Extended manual position bar.beat.division.tick is accepted to round-trip fractional positions; traditional bar or bar.beat remain supported.

`theory.py`: `note_name(pitch, prefer_flats=False)`, `transpose(symbol, semitones, prefer_flats=False)`, `display_chord(symbol)`, `number(symbol,key)`, `parse_key(text) -> MusicalKey|None`, `analyze_sections(document) -> tuple[KeySection,...]`. Preserve Swift behavior, deterministic tie breaking; do not include UI dependencies.

`project.py`: `load_project(path)->ChartDocument`, `save_project(document,path)->None` (atomic); `demo_document()->ChartDocument` (original demo embedded/loaded from packaged assets). schemaVersion=1; fields name,bars,bpm,meter,events [{id,bar,tick,symbol}],forcedKey,manualSections,detectChanges,originalKey,loop. Keys `{root,minor}`, sections `{bar,key}`, loop `{startBar,endBarExclusive}`. No audio/window/network prefs. Validate completely before replacing user state.

## Transport (A2)

`transport.py`: `StandaloneTransport(document, clock_ns=time.perf_counter_ns)`; property `document`; `set_document(document)` stops/replaces; `play()`, `pause()`, `stop()`, `seek(bar,beat=1.0)`, `set_bpm(bpm)`, `set_loop(loop:LoopRange|None)`, `set_meter(meter)`, `close()`; `snapshot(revision=1)->dict` ready for existing transport wire shape. Transport controls update their document via replace; UI reads it back to preserve edits. `HostAdapter` and `TransportController` protocols separate read vs control capabilities.

Sample fields: revision,sampleTime,readMs,valid,precise,rate,bar,beat,division,tick,bpm,meter (string e.g.4/4),playing,discontinuity. `rate` beats/sec; sampleTime monotonic milliseconds. Stop ->1.1. Opening -> stopped. Seek allowed through exclusive project end; no beyond end. Meter changes while stopped only, preserve bar/tick positions and reject invalidation.

Optional playback `{endBeat:number,startTime?:number,loop:null|{startBeat,endBeat,iteration}}`. Zero-based quarter beats, end exclusive. Start/resume prepare400ms; sampleTime remains real sample time. Natural loop increments iteration only, not discontinuity. Manual controls cancel old epoch. Clock keeps fractional phase internally, quantizes only serialized positions. Pause during preparation preserves position. Play at end restarts at start; enabled loop constrains position to loop range on play/seek. No timers required in pure transport.

## Audio (A4) / LAN (A5) / UI (A3)

A4 owns shared Resources/Broadcast.html, Metronome.js, new timeline JS and audio.py bridge/tests. `AudioPanel(parent=None)` QWidget; signal `enabled_changed(bool)`; `update_transport(payload, name='')`, `set_enabled(bool)`, `shutdown()`. Native restricted QWebChannel has clock and audioState; no network listener. Bundled resources path via `paths.resource_path(name)` (A0). Preserve WebKit legacy branch. Qt view persists across tab switches and minimalization; lock/suspend handled in platform lifecycle module by A0/UI.

A5 owns `lan.py` + protocol tests. `chart_payload(document,sections,revision=1)->dict`. `LANServer()`; `start()->list[str]` join URLs; `publish(chart:dict,transport:dict)`; `stop()`. GUI-safe synchronous methods; server owns background asyncio thread; publish coalesces immutable snapshots. No listener before start. Clock uses perf_counter_ns/1e6. Preserve current LAN routes/limits/security headers/private address ranges; chart precedes same-revision transport. All failures surfaced to caller, no daemon thread leak. No Resources edits by A5. Integration decision: use a small stdlib asyncio HTTP/SSE protocol to enforce connection limits before headers arrive and a total 8KiB/5s header budget; aiohttp is not shipped.

A3 owns `ui.py`, `render.py`, `settings.py`, associated tests. `MainWindow()` loads demo stopped/audio off; shared layout for chart and QPdfWriter. Native UI composes transport, AudioPanel, LANServer; 20ms snapshot timer, render/chart revisions only when content changes. Don't run theory analysis at50Hz. `main.py` bootstrap/paths and shared pyproject owned by A0. Settings via QSettings/AppData. All audible starts require explicit user action. No UI state stored in Program Files.

## Packaging/CI (A6)

A6 owns `windows/packaging`, `windows/scripts`, `.github/workflows` and package smoke tests. Root owns pyproject and lockfiles. Use cx_Freeze8.7.1; PySide6 6.11.2. Main entry `windows/src/chordcue/main.py` and Resources external bundled directory. QtWebEngine process/plugins/resources/locales/VC runtime/licenses required. Allusers MSI, ALLUSERS=1, ProgramFiles64Folder, stable UpgradeCode, downgrade block and transactional rollback. No auto-launch after install, PATH modification or firewall custom actions. User AppData remains after uninstall. CI push and PR; macOS original build plus Swift theory oracle, Windows tests and MSI artifacts. No claims consumer Windows/audio/LAN real-device validation passed merely from CI.
