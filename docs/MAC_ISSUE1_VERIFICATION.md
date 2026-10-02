# Issue 1 Mac verification

The Mac frontend packages the shared offline importers, alphaTab, fonts and
ScoreIR model. `MacScoreProject` reads the Windows-compatible v1/v2 envelope;
v2 retains every source part, staff, event and warning. The inner ScoreIR now
writes version 2 with structured techniques and written pitches; strict version
1 scores migrate with empty techniques. The selected part is
saved separately. An unsupported or damaged destination is rejected before
atomic replacement.

`LANDevices.swift` implements Python's protocol v2 registration, recovery,
independent assignment and telemetry/ACK contract. The host table never exposes
resume credentials. Request headers/body, registration count, stream count,
telemetry rate and stalled writes are bounded. Device health becomes stale
after six seconds without telemetry.

Independent transport samples keep source quarter offsets separate from route
occurrences. Full route JSON is sent with the chart and reused by `routeId` in
the native metronome. The chord grid and PDF use source duration, printable bar
number, meter and exact harmony offsets, including pickups and fractional
onsets. Native PDF rendering still needs visual QA on a Mac.

Run on a Mac with Xcode command line tools:

```sh
bash scripts/test-mac-score.sh
bash scripts/build.sh
```

The first script compiles a Foundation-only fixture oracle that checks complete
project round trips, v1 compatibility, invalid selection, duplicate keys,
safe destination preservation, per-device assignment, exact revision ACKs,
stale health and fragmented POST handling. The second compiles the native UI,
WebKit bridge, network listener and independent transport.

Manual native checks after compilation:

1. Import each `windows/tests/fixtures/issue1-original.*` MusicXML/GP source;
   inspect the part selector, warning list and actual rendered staff/TAB.
   Cancel the preview and confirm the active project remains intact.
2. Import a chosen part, save, reopen, and confirm all parts are still selectable.
   Show chords/numerals only where explicit source annotations are available.
3. Switch to independent playback with Logic closed. Check play/pause/stop,
   source-measure seek, loop, pickup, variable meter/tempo, repeat and ending
   positions. Toggle sound through the existing native metronome.
4. Enable LAN, join two browsers, assign different parts/views and labels, then
   confirm each device's ACK is tied to the assignment and score revisions.
   Disconnect/reconnect one browser and inspect health/RTT/offset.
5. Import an invalid/truncated file and use a future-schema file as a save
   destination. Confirm no active project or destination content is replaced.
6. In the score page, switch staff/TAB and parts. Confirm a part without source
   string/fret data disables TAB and falls back to staff. Display transposition
   must preserve TAB fingering. A legacy staff/TAB preference should open the
   score page once rather than putting that selector on the chord page.
7. Play a multi-row score and compare local WebKit with a LAN browser. The next
   actual rendered system and the final system should reach the visible top,
   below the browser header. Resize, loop, seek, pause/resume and toggle follow;
   follow off preserves manual scroll and note highlights.
8. Check note-head/TAB boxes for simultaneous voices, chord notes, held notes
   and rests. Pause preserves the box; hidden view, stale data, reload and
   disconnect clear obsolete boxes. Import both `techniques.musicxml` and
   `techniques.gp`, inspect H/P arcs, slides, bends, vibrato, harmonics, palm mute
   and dead notes, then save/reopen. Missing harmonic nodes must not invent a
   fret or octave. Unsupported effects must carry source warnings.
9. Check clock status, jitter and probe age rather than treating the host/browser
   monotonic origin difference as latency. Run beyond the ten-second probe
   window: healthy probes continue, freshness follows the latest successful
   probe, and replacing the lowest-RTT estimate must preserve scheduled beats.
   Normal offset adjustment is limited to 5 ms per second. Initial calibration,
   expiry/timeout, sleep/resume, host restart and a genuine mapping jump should
   mute audio until calibration and transport recover. Test an older client
   without diagnostics telemetry.
10. Join staff/TAB devices with a valid score larger than 2 MiB. Check complete
    source delivery, independent assignments, exact revision ACKs and playback
    highlights. Read a chart slowly while revising it: each SSE frame remains
    intact, only the latest pending revision survives, and other control
    requests remain responsive. A stalled chunk should disconnect; an active
    transfer may take more than five seconds overall. Exercise the 256 MiB
    distinct active-frame budget with different old revisions and confirm the
    oldest holders reconnect to the latest chart. Stop must release frame and
    publication caches.
11. Inspect **音频输出延迟估计** in the device table. An uncreated/non-running
    AudioContext or unavailable device estimate must display **未提供估计**.
    A preview-created running context may report an estimate even with the
    metronome toggle off. With controlled attributes, check positive output
    latency with/without base latency, base-only latency, explicit zeros and
    invalid values. Base-only values belong to the local processing display;
    their device-output telemetry remains null. Verify that valid output
    timestamps still drive scheduling and that manual advance remains separate.

Current validation limitation: these native commands and manual checks have
not been run in the Windows development workspace. No Swift compiler or Mac
runtime is available here. Shared JavaScript/Python checks do not establish
native Swift compilation, WebKit behavior or LAN socket lifecycle success.

## Historical 0.3.1 / build 4 acceptance

Windows 0.3.1 integration passed 83 shared Node tests with
`--test-isolation=none`, including import/techniques/render/device/clock/metronome.
The complete Python/Qt run passed 457 tests, including final MSI/frozen-artifact
checks; 17 Swift-dependent checks were skipped because that runtime is absent.
Real Qt staff/TAB rendering and LAN row alignment were checked on the Windows
host. That acceptance used Info.plist 0.3.1, build 4. These results do not
validate native Mac code; the source changes still require the commands and
manual checks above.

## Historical 0.3.2 / build 5 changes and focused verification

That version used Info.plist 0.3.2, build 5. Healthy clock calibration now
continues probing and uses the latest successful probe for freshness. Ordinary
offset correction is smoothed at no more than 5 ms per second and preserves
already scheduled metronome beats. First use without calibration, calibration
expiry after successful probes time out, and genuine clock jumps still mute
audio until a valid mapping and fresh transport recover.

LAN broadcasts a complete ScoreIR once per new chart revision; each device
selects its assigned source part locally. Score and route each retain a 32 MiB
JSON budget, score-chart metadata is limited to 1 MiB, the combined chart to
65 MiB, and transport to 64 KiB. Source chord/key adapters are not duplicated
beside the complete score. Continuous SSE frames use at most 64 KiB chunks with
a five-second stalled-chunk timeout. Assignment, heartbeat and state events
wait for the active frame to finish; pending chart/state updates coalesce to
the latest revision. Shared distinct active chart frames have a 256 MiB budget,
with the oldest holders cancelled when admitting a new frame would exceed it.
This budget does not represent whole-process RSS. Completion/cancellation
releases peer frame references, and stop also clears publication caches.

Focused Windows verification has passed real TCP delivery of a valid
approximately 2.4 MiB ScoreIR and real Qt staff/TAB rendering, independent
assignment ACKs and playback highlights. The protocol regression also passed.
The Mac changes have only been reviewed statically in this workspace: neither
`swift` nor `swiftc` is available, and native compilation, Network.framework
transfer behavior, WebKit rendering and playback have not been run. Run the
Mac commands and manual checks above before recording native acceptance.

## 0.3.4 / build 7 timing presets

In 0.3.5 / build 8 the manual timing sheet and add-change controls have been
removed. Current BPM / meter remain read-only and imported source maps still
drive playback. Verify GP / MusicXML automatic changes without opening any
editor, and reopen the saved v3 project to confirm compatibility. The historical
0.3.4 editor steps below now apply to an existing saved fixture, not the current UI.

The local source now includes saved bar-start BPM / meter editing for source
scores and manual projects, envelope v3 manual persistence, an ephemeral manual
ScoreIR route, LAN measure metadata and variable-duration PDF layout. Existing
source intra-bar tempos and guitar techniques must remain intact. This Windows
run cannot compile or exercise AppKit / WebKit; these are required Mac checks:

1. Run `scripts/test-mac-score.sh` and `scripts/build.sh`, including the timing oracle.
2. Create a manual project with 4/4 ♩=120, bar 2 at 6/8 ♩=90, bar 3 at 7/8 ♩=150;
   save, reopen, play, seek and loop. Confirm actual displayed BPM / meter and
   native metronome switch at source bar boundaries.
3. Apply a shorter meter that would exclude an existing chord or note. Confirm
   the error identifies the bar and the original project and playback survive.
4. Edit only BPM in an imported techniques score; verify unchanged note/technique
   IDs, TAB fingering, pickups, mid-bar tempos and repetitions after saving.
5. Join a LAN client late, seek and update the table; check route revision,
   personal view, per-bar beat labels, following and metronome. Export manual
   and source PDFs and check source onsets and actual bar capacities.
6. Check 1000 loop endpoints and samples immediately before/after the boundary;
   no stale end-of-loop bar or extra/missing onset may occur.

## 0.3.3 / build 6 changes and focused verification

Info.plist now declares 0.3.3, build 6. The device table uses
**音频输出延迟估计** and displays **未提供估计** for null telemetry. The shared
metronome retains valid `getOutputTimestamp()` scheduling, while output-delay
diagnostics use reported latency attributes. The [W3C getOutputTimestamp note](https://www.w3.org/TR/2021/REC-webaudio-20210617/#dom-audiocontext-getoutputtimestamp)
explains why the difference from `currentTime` is unsuitable as a reliable
output-latency estimate.

Only an existing, running AudioContext can supply diagnostics; a running sound
preview is eligible even if the metronome toggle is off. A finite positive
`outputLatency` supplies the device-output estimate. Valid nonnegative
`baseLatency` adds its known processing portion; missing base latency leaves
an explicitly labelled device-only estimate. Base latency alone, absent or
invalid output latency, and output latency zero leave device-output telemetry
null. Browser-reported zeros do not demonstrate physical zero latency. Manual
advance, scheduling mapping, network RTT and clock offset remain separate.

Focused Windows verification has passed a real Qt WebEngine AudioContext with
controlled latency properties. Browser diagnostics flow through registration
and POST telemetry to the host registry: reported zero remains unavailable,
8 ms processing plus 24 ms output reports 32 ms, and suspension restores null.
The actual device table also consumes these registry values and displays
unavailable/32.0 ms/unavailable in the completed full regression.
The 101 targeted Python/Qt/LAN checks and 103 shared JS tests have passed;
scheduling regressions preserve valid output timestamp mapping and independent
manual advance. Full candidate acceptance totals 465 Python/Qt/LAN/packaging
passes and 17 Swift-dependent skips, combining source/frozen tests with the
deferred final MSI policy check. Three frozen-application smokes and the MSI
audit also passed. These results are recorded in local outputs and do not
establish native Mac acceptance.

Controlled properties verify the telemetry and display chain; physical audio
device latency and absolute acoustic synchronization remain unmeasured. The
Mac frontend still has no native compilation/runtime acceptance in this
Windows workspace because `swift`/`swiftc` and a Mac runtime are unavailable.
