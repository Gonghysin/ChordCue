# Issue 1 score and project contract

The interchange representation is `ScoreIR` version 2. It is independent of
alphaTab and of either desktop UI. Python's immutable types live in
`windows/src/chordcue/score_models.py`; Swift's Codable types live in
`Sources/ScoreModels.swift`. JSON uses camelCase and `Resources/score/score.schema.json`.

## Time and identity

Every duration and offset is an exact **quarter-note** rational object
`{"numerator": 1, "denominator": 3}`. Fractions are reduced on construction.
Offsets are zero based within their referenced source measure. Source measures
stay in score order, have globally unique stable string IDs and retain their
original printable `number` (including pickups and nonnumeric labels).
`duration` is actual source length, so a pickup need not equal its effective
`meter` (`{"numerator":6,"denominator":8}`). Measure numbers are display labels,
not playback positions. Repeat occurrences must refer back to the same measure
ID; never duplicate or renumber the stored score to make a playback route.

IDs match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` and are globally unique across the
score, parts, staves, events, notes, chords, and markers. Importers generate
deterministic source IDs, e.g. `m1`, `p1`, `p1.s1.e1`, rather than random UUIDs.

## Canonical JSON shape

`ScoreIR`: `formatVersion:2`, `id`, `title`, `source`, `measures`, `parts`,
`tempoChanges`, `keyChanges`, `warnings`. All arrays are present, even if empty.

* `source`: `{format, fileName, sha256}`. Format is `musicxml`, `guitarpro`, or
  `manual`; nullable original basename and SHA-256 identify the source without
  retaining an absolute path or vendor AST.
* `measures[]`: `{id, number, duration, meter, repeatStart, repeatEnd,
  endingNumbers, markers, navigation}`. `repeatStart` is boolean;
  `repeatEnd` is null or total traversal count 2..32. `endingNumbers` is the
  applicable set of passes, e.g. `[1]`/`[2]`, on **every** measure in that ending.
  Expand source ending start/stop spans into these memberships during import.
* `markers[]`: `{id, kind, label, offset}`. Kinds: `section`, `rehearsal`,
  `segno`, `coda`, `fine`. Label is original text, offset may equal bar end.
* `navigation[]`: `{kind, targetMarkerId, offset}`. Kinds: `dc`, `ds`, `toCoda`,
  `fine`. Offset may equal bar end. A nonnull target must reference a marker;
  unresolved source targets remain null **and carry a structured warning**.
  Repeat/jump execution belongs to the route compiler, not this data model.
* `parts[]`: `{id, name, instrument, staves, chords}`. Instrument is nullable
  original descriptive text. Each part contains at least one staff.
* `staves[]`: `{id, name, kind, clef, tuning, capo, events}`. Kinds: `standard`,
  `tab`, `percussion`. Clef is nullable original text. Tuning is sounding MIDI
  pitches in **string-number order** (string 1 first, conventionally highest),
  not ascending MIDI order; empty means unavailable. Capo defaults to 0.
* `events[]`: `{id, measureId, offset, duration, voice, isRest, grace, notes, techniques}`.
  Voice is a positive source voice normalized to 1..16. Rest has no notes;
  pitched or unpitched event has at least one. Non-grace duration is positive;
  grace duration may be zero. Simultaneous notes belong to the same event.
  A non-grace event must fit within source measure duration. No generated chords.
* `notes[]`: `{id, pitch, string, fret, tieStart, tieStop, unpitched, accidental, writtenPitch, techniques}`.
  Pitch is nullable sounding MIDI 0..127. String/fret are nullable, supplied
  together, string one based and fret zero based; valid string references must
  fit staff tuning when tuning is known. Pitch may be null for unpitched notes
  or supplied TAB string/fret. Accidental is nullable source spelling text.
  `writtenPitch` is nullable MIDI 0..127 for the original notation pitch before
  instrument transposition or a bend; null means unavailable. GP `pitch` uses
  `realValue`, which already includes harmonic sounding changes. GP
  `writtenPitch` uses `displayValueWithoutBend`. Neither stored pitch includes
  an additional bend/whammy curve offset. A renderer must not raise a harmonic
  sounding pitch a second time. MusicXML retains its explicit pitch spelling
  before `<transpose>` in `writtenPitch`; ambiguous harmonic pitch roles warn.
* `chords[]`: `{id, measureId, offset, text, staffId}`. Preserve **original**
  harmony/chord annotation text, including `N.C.` and unsupported names. Staff
  is nullable (part-wide) or a staff in that part. Chords arise only from explicit
  source annotations. Notes-only scores have empty chord arrays.
* `tempoChanges[]`: `{measureId, offset, bpm}`. BPM is quarter-note tempo 1..1000,
  finite. Offset may equal source bar end, carrying forward without a zero-length
  playback segment. Same-location duplicates are invalid. No tempo map implies 120 BPM;
  source non-quarter metronome units must be converted by the importer.
* `keyChanges[]`: `{measureId, offset, fifths, mode}`. Fifths -7..7, mode `major`,
  `minor`, `unknown`; offset may equal bar end. Same-location duplicates are invalid. No key map means
  unknown; note content must not be used to invent a key or chord annotation.
* `warnings[]`: `{code, message, severity, measureId, partId}`. Severity `info`
  or `warning`; references nullable. Codes are stable ID-shaped strings, with
  human-readable single-line messages. Preserve unsupported/ambiguous rhythm,
  jumps, endings, metadata, or source features as warnings visible in preview.

Python class names: `QuarterFraction`, `ScoreMeter`, `ScoreSource`,
`ScoreMarker`, `ScoreNavigation`, `ScoreMeasure`, `ScoreNote`, `ScoreEvent`,
`ScoreStaff`, `ScoreChord`, `ScorePart`, `ScoreTempoChange`, `ScoreKeyChange`,
`ScoreWarning`, `ScoreBendPoint`, `ScoreTechnique`, `ScoreIR`. Python field names are snake_case counterparts;
`ScoreIR.to_dict()` / `ScoreIR.from_dict(data)` are the only JSON boundary.
`QuarterFraction.as_fraction()` returns stdlib `fractions.Fraction`.

## Guitar techniques

Every technique has the strict complete shape
`{kind, targetNoteId, value, direction, curve}`. Unused fields are explicitly
null and unused curves are `[]`. A note or event contains at most 32 techniques;
duplicate kind/direction pairs are invalid. No vendor AST is stored.

| kind | direction | value | association |
| --- | --- | --- | --- |
| `hammerOn`, `pullOff` | null | null | target note or null |
| `slide` | `shift`, `legato`, `inAbove`, `inBelow`, `outUp`, `outDown` | null | target for shift/legato only |
| `bend`, `whammy` | `bend`, `prebend`, `release`, `prebendRelease` | null | normalized curve |
| `vibrato` | `slight`, `wide` | null | note or event |
| `harmonic` | `natural`, `artificial`, `tap`, `pinch`, `semi`, `feedback` | nullable touch-node fret 0..99 | note |
| `pick` | `up`, `down` | null | note or event |
| `tremoloPicking` | null | subdivision denominator 8,16,32,64,128,256 | event |
| `trill` | null | nullable target MIDI 0..127 | note |
| `brush` | `up`, `down`, `arpeggioUp`, `arpeggioDown` | nullable inter-string delay in quarter notes 0..16 | event |
| `palmMute`, `deadNote`, `letRing`, `staccato`, `accent`, `heavyAccent`, `ghost` | null | null | note |
| `tap`, `slap`, `pop` | null | null | note or event |

Event techniques are restricted to `vibrato`, `pick`, `tremoloPicking`, `tap`,
`slap`, `pop`, `brush`, `whammy`. Keeping beat effects separate prevents a chord
from multiplying whole-beat effects; `brush`, `whammy`, and `tremoloPicking`
cannot be attached to a note. Ties and grace notes retain their existing
fields. A cross-note target must exist later in the same staff and voice and,
when both positions are known, on the same string. A grace origin may point to
a later event's principal note at the same offset; self-links and reverse links
between simultaneous grace events are always invalid.
Importers retain unresolved starts with null targets and a source warning.

Curve points are strict `{position, semitones}` objects: position is normalized
0..1 through the source event, semitones are finite -24..24 relative to the
unbent note. At most 64 points, nondecreasing positions, with 0 and 1 endpoints.
GP units convert from offset/60 and value/2. MusicXML bend-alter gives semitones;
absent source timing uses symbolic endpoints for display. These data preserve
notation; they do not introduce an instrument sound engine.

GP imports preserve the above note and beat effects. Unsupported pickslides,
tenuto, fingerings, independent slurs, fade/crescendo/rasgueado, trill speed, and
non-default tremolo styles produce explicit warnings naming source event/note,
measure and part. Curves outside the contract warn instead of being truncated.
Raw GPIF note and beat nodes are also audited before vendor normalization can
discard an unfamiliar effect: unknown elements/properties name the GPIF source
ID and trace its master bar and part. Legacy GP3/4/5 uses its parsed binary
effect enumerations; GP6/7/8 additionally uses this raw GPIF audit.
MusicXML imports use structured technical, articulation and ornament nodes.
`other-technical` prose is never interpreted as a technique. XML slide/glissando
start/stop links are retained with a warning that the source does not distinguish
shift from legato; the display adapter uses shift. Unsupported nodes, missing
endpoints and ambiguous harmonic pitch roles also warn at the source location.

Original MIT technique fixtures include `techniques.musicxml`, a real GP8
`techniques.gp`, and canonical v2 JSON projections. Tests check import, JSON
reopen, atomic project save/reopen, link/curve rejection, and v1 migration.

## Project compatibility and limits

Append `score: ScoreIR|None` and `selected_part_id: str|None` to existing
`ChartDocument`; existing fields and old constructors remain supported.
Selection must reference a part; nonnull selection without a score is invalid.
The imported score remains authoritative and complete; legacy `events` are an
optional explicit-chord display adapter, never the saved replacement for notes.
`document_with_score(score, selected_part_id=None)` builds the compatibility
envelope without synthesizing legacy chord events; import controllers may add
an explicit display adapter separately.

Saving a document without a score emits **unchanged schemaVersion 1**. Saving
with a score emits schemaVersion 2 and includes `score` and `selectedPartId`
alongside existing fields. Version 2 requires those two fields; version 1 may
not silently contain them. Load validates completely and returns a new document;
save validates and serializes before atomic sibling-file replacement. An invalid
or future existing destination is never overwritten.

Project envelopes stay at schema 1/2. A schema 2 project may embed ScoreIR v1
or v2. All runtimes strictly validate v1's original fields before migrating it
to v2 with empty event/note techniques and null writtenPitch. Saving writes v2.
Techniques discarded by an older importer cannot be reconstructed from its
saved JSON; re-import the original MusicXML/GP file to recover them. Future
score versions and unknown technique fields/kinds fail before any replacement.

Limits: project/score JSON 32 MiB, 10,000 source measures, 64 parts, 16 staves per
part, 500,000 events, 1,000,000 notes, 100,000 explicit chords, 10,000 warnings,
and 2,000,000 total bounded JSON nodes.
Total techniques are additionally limited to 200,000; individual curve points
and techniques remain bounded as above. Fraction numerator magnitude <=2^31-1,
denominator 1..1,000,000; measure duration <=4096 quarters. Effective meters have
numerator 1..64 and denominator 1,2,4,8,16,32,64. Text has field-specific limits
and rejects controls. There is no new third-party core dependency.

## LAN publication and payload limits (0.3.2 / build 5)

A new chart revision broadcasts the complete authoritative ScoreIR and its
compiled playback route to every connected device. Each device applies its
assigned part/view locally; the broadcast is not a per-part score projection.
Score charts do not repeat source chords or key maps in the legacy top-level
`events`/`sections` adapters. Routes belong to the revisioned chart and are
omitted from recurring transport samples. Registration, assignment and applied
revision ACKs remain separate small control messages.

The following limits count UTF-8 JSON payload bytes, before the SSE framing:

| Payload | Limit |
| --- | --- |
| Complete chart | 65 MiB |
| Embedded ScoreIR | 32 MiB |
| Embedded playback route | 32 MiB |
| Remaining metadata in a score chart | 1 MiB |
| Transport sample | 64 KiB |

Oversize payloads fail explicitly rather than dropping source parts or
truncating the route. Request headers and registration/telemetry POST bodies
retain their independent 8 KiB limits.

Chart SSE frames are sent as consecutive chunks of at most 64 KiB; Windows may
use smaller chunks when its configured write high-water mark is lower. A
stalled chunk/write drain has a five-second timeout, rather than applying a
five-second deadline to the whole chart. No assignment, heartbeat, transport
sample or replacement chart may be inserted inside an unfinished SSE frame.
After the active frame finishes, only the latest pending chart/state is sent.

All peers share immutable chart frames. Distinct active chart frames, including
a candidate frame before it starts, have a combined 256 MiB budget. Exceeding
it cancels connections holding the oldest active frames so they can reconnect
to the latest chart. This is a serialized active-frame budget, not a bound on
whole-process RSS, parsed ScoreIR, browser rendering or all socket allocations.
A completed peer frame releases its body; the enabled host retains the latest
shared chart for reconnects. Cancel/stop releases active and pending frames,
and stop also clears the prepared/identity publication caches.

## Playback route and clock

`PlayPlan` / `ScorePlayPlan` compile source measures to bounded occurrences,
retaining original source IDs. Route JSON is `{version:1,endQuarter,
durationSeconds,occurrences,segments,warnings}`. An occurrence is
`{id,sourceMeasureId,sourceIndex,sourceNumber,startQuarter,endQuarter,meter}`;
index is one based, occurrence ID is `sourceId@visit`. A segment is
`{startQuarter,endQuarter,startSeconds,endSeconds,bpm,occurrenceId,
sourceMeasureId,sourceOffsetQuarter}`. Wire quarter positions are finite numbers;
source fractions and route arithmetic remain exact. Route duration <=1,000,000
quarters, common rational denominator <=1,000,000, occurrences <=100,000,
tempo segments <=200,000, repeat nesting <=8, route JSON <=32 MiB.

Written repeats/numbered endings are expanded into occurrences. DC/DS execute
once, then written repeats are omitted and final endings selected. To Coda/Fine
are armed after that jump. Targets must be resolved source markers at offset 0;
navigation must be at source bar end. Unresolved, mid-measure, overlapping or
ambiguous structures carry route warnings and retain safe source continuation.
Preview must show these route warnings alongside import warnings.

Transport integrates elapsed monotonic seconds through tempo segments. Play and
resume prepare 400 ms. Manual controls advance `discontinuity`; natural repeats,
loop iterations and score end do not. Imported `set_bpm` scales all tempo-map
values while preserving their ratios; `set_meter` requires editing the source
map. This speed override is session-only: re-opening a score restores its source
tempo map, while project `bpm` is still the legacy compatibility fallback.
`setTempoScale` on Swift has the same transient meaning; no source tempo metadata
is rewritten and no new persistence field is implied. Selection-only
`select_part`/`selectPart` preserves phase and discontinuity.
Source seek can specify a one-based occurrence; slider seek uses route quarters.
Source loop selection uses the first continuous performed passage through that
source interval; preview should expose the resulting occurrence bounds.

Score snapshots add `playQuarter` (`positionQuarter` alias), `sourceMeasureId`,
`sourceOffsetQuarter`, `occurrenceId`, `meterNumerator`, `meterDenominator`,
`selectedPartId`, `routeId`, `preparing`; old transport fields remain supported.
`rate` is quarters/second; meter beat unit is `4/denominator` quarters. Routes
are sent once with revisioned chart data, then cached by `routeId`; Python
`snapshot(include_route=False)` avoids repeating them over LAN. `to_dict()`
returns a safe copy; `to_dict(copy=False)` is a read-only internal cache view.

`ChordCuePlaybackPlan` in `PlaybackPlan.js` exports `validateRoute`, `secondsAt`,
`quarterAt`, `occurrenceAt`, `position(route,sample,hostTimeMs)`,
`unwrappedQuarter`, `targetTime(route,sample,unwrappedQuarter)` and `allows`.
Prediction uses the same monotonic millisecond domain as sample/startTime. For
loops, unwrapped quarter = wrapped quarter + iteration * loop quarter length.

### Clock calibration diagnostics

Host/browser monotonic clocks may have unrelated origins. `clockOffsetMs` is
the signed origin mapping used for prediction, not a user-visible latency.
Valid four-timestamp probes require finite ordered timestamps, nonnegative
RTT, and a matching outstanding probe/session. Calibration chooses the lowest
RTT valid probe within the ten-second window. In 0.3.2 / build 5, probing
continues while calibration is healthy. `probeAgeMs` and calibration freshness
use the latest successful probe, independently of the age of the lowest-RTT
estimate. A new successful probe therefore keeps a healthy mapping valid when
an older lowest-RTT estimate leaves the window.

Ordinary drift corrections approach the chosen estimate by at most 5 ms per
second. Already scheduled metronome onsets keep their scheduled times; normal
calibration does not cancel those beats, and the refined mapping applies to
subsequently scheduled beats. First use without calibration, calibration
expiry after successful probes time out, and a genuine clock mapping jump
still mute/cancel audio until a valid mapping and fresh transport are available.
Resume and reconnect
invalidate prior probes. Sample ages must be in `[-30,350]` ms for
prediction; future/stale samples clear highlights and cancel metronome sources.

Registration responses advertise `serverCapabilities:["clockDiagnosticsV1"]`.
Only a client that sees that capability sends telemetry
`clockDiagnostics:{version:1,status,jitterMs,probeAgeMs}`. Status is `valid`,
`calibrating` or `stale`; the numeric fields are finite nonnegative values or
null. Jitter is the RMS spread of recent origin estimates. Old telemetry stays
accepted and displays unavailable calibration quality. Credential recovery,
assignment revisions and score ACK behavior are unchanged.

### Saved bar-start timing presets (0.3.4 / build 7)

As of 0.3.5 / build 8, the native manual timing editor and add-change controls
have been removed. Imported scores continue to follow their existing tempo
and meter maps automatically, including mid-bar tempos and repeated source
measures. The UI displays current BPM and meter as read-only status. Existing
v3 timing projects remain readable and playable; the wire and persistence
contracts below are retained for compatibility, not exposed as a manual editor.

`TimingChange` uses `{bar, bpm, numerator, denominator}`. `bar` is the one-based
source measure index, not a displayed source number or expanded occurrence.
The first row must be bar 1; later rows are strictly increasing, unique, and
within the project. Each row inherits until the next row. BPM is always quarter
notes per minute (1–1000), meter numerator is 1–64, and denominators are
1, 2, 4, 8, 16, 32, or 64. For example, 6/8 at ♩=90 lasts three quarters,
or two seconds; its six eighth-note beats are separated by 1/3 second.

Manual projects without a timing table remain envelope v1. Manual projects with
`timingChanges` use envelope v3, retaining legacy text events and key analysis;
they cannot also contain `score` / `selectedPartId`. The internal ephemeral
manual ScoreIR only compiles a route and is not stored in the project. Such
projects support at most 10000 bars, matching the shared route budget. Their
event ticks and textual beat/division positions still count quarters at PPQ=960.
Envelope `bpm` and `meter` are bounded legacy fallbacks, not authoritative timing.

Source projects remain envelope v2. Applying a table changes their authoritative
ScoreIR at bar starts, preserving intra-bar tempos, note/rest durations and
onsets, guitar techniques, key/source data, and explicit chords. A full ordinary
bar takes the new meter length. A pickup or irregular duration is retained;
when its meter changes it must fit the new capacity. Unchanged irregular bars
may retain a duration larger than their nominal meter. Metadata attached to the
old bar endpoint follows the new endpoint; interior metadata retains its offset.
Any note/chord/interior metadata that no longer fits rejects the entire edit.
Saving keeps the edited score; the source hash continues to identify the imported
file and does not claim it contains the edits.

Candidate validation includes route compilation and existing loop bounds before
installation. Success stops playback and returns to the start; failure leaves
the running document unchanged. Windows and Mac LAN chart revisions include
the changed route. For manual timing projects, `chart.measures` carries ordered
`id`, `sourceNumber`, quarter `duration`, and `meter`; IDs match route occurrences.
Legacy events remain available for chord/number views, and no note part is
assigned from the ephemeral adapter. Clients predict BPM/meter using the route.
Loop cycle/phase normalization uses double-precision tolerances capped at 0.5 ns
so mathematical loop boundaries agree without consuming a real preceding beat.

### Audio output delay estimates (0.3.3 / build 6)

The device table labels `audioOutputDelayMs` as **音频输出延迟估计**. A null
value displays **未提供估计**, rather than a numeric zero or an applied
compensation amount. The field remains compatible with the existing telemetry
shape and represents browser-reported latency estimates in milliseconds.

Scheduling continues to prefer a valid `getOutputTimestamp()` mapping between
audio and performance clocks. Diagnostics do not derive output latency from
`currentTime - timestamp.contextTime` or its projected equivalent. W3C explains
that this difference is unsuitable for reliable output-latency estimation
because `currentTime` advances nonuniformly, and points to `outputLatency`
instead. See the [Web Audio specification's getOutputTimestamp note](https://www.w3.org/TR/2021/REC-webaudio-20210617/#dom-audiocontext-getoutputtimestamp).

Diagnostics require an existing AudioContext in `running` state. A running
context created by sound preview is eligible even when the metronome enable
toggle is off. An uncreated, suspended, interrupted or closed context reports
null. For a running context, attributes must be finite and within their valid
nonnegative domain; `outputLatency` must additionally be strictly positive to
provide a usable device-output estimate.

| Browser values in a running context | `audioOutputDelayMs` | Local explanation |
| --- | --- | --- |
| Positive `outputLatency`, valid nonnegative `baseLatency` | `1000 * (baseLatency + outputLatency)` | Processing and device-output estimate |
| Positive `outputLatency`, unavailable/invalid `baseLatency` | `1000 * outputLatency` | Device-output portion only; processing portion unavailable |
| Only positive `baseLatency`; `outputLatency` missing, invalid or zero | null | Browser processing estimate only; device-output estimate unavailable |
| Missing/invalid attributes or all explicitly zero | null | No usable output estimate; browser-reported zero does not establish physical zero latency |

The local view identifies the estimate's scope and the scheduling mapping
source. A delay estimate is neither measured acoustic synchronization error nor
the amount of correction applied to a scheduled beat. Manual advance remains
an independent user setting and is not added to `audioOutputDelayMs`; clock
offset and network RTT also remain separate diagnostics.

### Score presentation

`ScoreView.setPosition` receives source measure/quarter plus playing, preparing,
valid, occurrence, discontinuity and loop iteration. Note intervals are start
inclusive / end exclusive and include simultaneous voices and generated rests.
The external overlay uses the rendered note-head or TAB bounds; alphaTab's
instrument player remains disabled.

`setFollow(bool)` controls scrolling independently from highlights.
`setActive(bool)` suppresses overlays/scrolling for hidden pages. Actual renderer
staff-system bounds determine rows; a fixed browser header is excluded from the
visible top. The bottom spacer permits final-system alignment. Pause preserves
the current box without scrolling; invalid/null positions clear it. Rerender,
resize and font/layout changes invalidate geometry before replaying the latest
position. Windows and WebKit bridges preserve the full position metadata.
