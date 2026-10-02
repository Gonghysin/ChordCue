# Independent Windows review (A7)

Review dates: 2026-09-29 and 2026-10-01. Feature issue: https://github.com/Gonghysin/ChordCue/issues/2.
Baseline: `726df0b3a0e0b1d750aa009aaa753ab424226515`; working branch:
`codex/windows-desktop`. The final check examined commits `c4e7c64` and
`44110e1` plus the integrated working-tree changes on 2026-10-01.
The reviewer changed only this document and `windows/tests/test_regression_qa.py`.
Implementation repairs were made by the integration and desktop owners.

**Final source-review status:** all reproduced findings below are repaired and
the independent full suite is green. No unresolved source blocker was found in
the reviewed scope. Subsequent CI and artifact evidence is recorded below;
clean consumer-Windows installation and real-device gates remain unverified.
This is not release or PR approval.

## Environment and scope

- Local host: Windows 10 build 19045 (not a clean VM).
- CPython 3.13.15 x64, PySide6 / Qt 6.11.2, Node.js 24.19.0.
- Reviewed domain validation and project save/open transactions, transport and
  optional playback timeline, Qt audio bridge, native controls and shared chart
  layout, LAN HTTP/SSE parsing and coalescing, MSI generation policy and CI.
- Reviewed CONTRIBUTING, the immutable baseline browser/native/LAN source, and
  `windows/CONTRACT.md` before implementation review.
- The local Qt audio regression probes use a fake AudioContext or call only the
  status bridge. They do not establish speaker output or acoustic timing.

## Findings and repair verification

| ID | Priority | Reproduction and consequence | Current verification |
| --- | --- | --- | --- |
| A7-01 | P1 | Queue native audio disable while an older JavaScript call is in flight; its delayed enabled notification cleared the newer pending disable. Audio could remain enabled after explicit disable/suspend. | Repaired with command sequence acknowledgments, pending-command protection and immediate native mute. Pending-disable, stale-sequence-after-send, and actual Qt rapid-command/page-hide probes pass. |
| A7-02 | P2 | Reopen the current dirty project and choose Save. `open_project_path` loaded the old document before prompting, saved the edit, then replaced the GUI with that old snapshot and marked it clean. | Repaired by rereading the target after the save/discard decision; same-file regression passes. |
| A7-03 | P2 | Stop at 1.1 while a 4/4 loop covers bars 2–3. The shared browser timeline wrapped stopped absolute beat 0 into beat 8 and displayed bar 3, disagreeing with the native transport. | Repaired by preserving absolute stopped position; real shared-JS regression passes. |
| A7-04 | P2 | Open a small JSON with `bars: 2147483648`. Core validation accepted it, then Qt raised OverflowError after the transport/project state had already been replaced. | Repaired with a 100000-bar validation ceiling before UI replacement; invalid-file state-preservation regression passes. Large-chart interactive performance is not established by this check. |
| A7-05 | P2 | After a native audio command, reload the WebEngine page. The native command sequence remained nonzero while the fresh JavaScript bridge reset to zero, so fresh page audio notifications were rejected. | Repaired with a new page epoch, a synchronized disarm command and rejection of callbacks from previous pages. The previously failing actual Qt reload regression now passes. |
| A7-06 | P2 | Source review found that the new dense-pagination validator could reject an overlong chord after QPdfWriter had opened the destination, risking destruction of an existing export. | Pagination now completes before any output opens; PDF generation uses a temporary sibling, validates completion and atomically replaces the destination. Existing-file preservation on invalid pagination passes. The source-order defect was repaired before the independent runtime probe, so no pre-fix runtime failure is claimed. |

The first full suite also exposed a timing-dependent LAN test failure:
`test_slow_client_is_bounded_coalesces_latest_and_disconnects` did not observe a
paused socket within its deadline. It passed in isolation. A5 changed the probe
to fill the actual Windows TCP receive window before asserting persistent
backpressure. The subsequent full independent suite passed; this is a local TCP
backpressure check, not cross-device LAN evidence.

The initially exposed Logic-text import chooser was flagged for scope review.
The integration owner classified it as local text input and removed the exposed
Logic-specific chooser while retaining the internal original-demo/oracle parser.

## Commands and results

Initial independent full run: **327 passed, 8 skipped, 1 failed** (LAN timing
probe above). The first four new regression tests all failed on their respective
defects before repair, then all passed after repair.

Final independent run on 2026-10-01, after audio reload, dense rendering and
atomic PDF export repairs:

```powershell
windows/.venv/Scripts/python.exe -m pytest -q windows/tests
# 345 passed, 8 skipped in 9.33 seconds
windows/.venv/Scripts/python.exe -m ruff check windows/src windows/tests windows/packaging windows/scripts
# All checks passed
git diff --check
# No whitespace errors; Git reported only working-tree CRLF conversion notices
```

The final suite includes all eight A7 regressions. The rapid audio command
probe uses a manually resolved fake resume promise, avoiding a narrow timer
window. It exercises the real Qt page and QWebChannel without opening an audio
output device. The reload regression failed before A7-05 was repaired and
passes in the final run. The earlier full run of 336 passed / 8 skipped predated
the reload finding and is superseded by the result above.

The eight skips are six actual Swift-oracle comparisons (macOS/Swift execution
not available here) and two final frozen/MSI artifact checks (artifact paths were
not supplied for this run). This preserves that run's history; subsequent CI and
artifact evidence closes those evidence gaps as described below.

## Static integration conclusions

- The transport retains fractional phase internally, serializes canonical
  960-PPQ positions, announces the 400 ms preparation start separately from the
  real sample timestamp, and preserves the discontinuity epoch on natural loops.
- The shared JavaScript includes finite-end exclusion and unwrapped loop
  identities. Legacy payloads without `playback` have explicit coverage.
- The final suite includes 29 render tests covering exact nearby onsets,
  full-name wrapping, dense-row continuation, dynamic hit testing, normal A4
  pagination and preservation of existing exports on layout/write failures.
  A7 also visually inspected the existing diagnostic images
  `windows/.diagnostics/edges/meter-7-1-fixed.png`, `meter-7-2-fixed.png` and
  `meter-12-1-fixed.png`: CJK headings, accidentals, dense entries, onset labels,
  page numbers and the second-page continuation were visible without clipping.
- LAN construction does not listen. Explicit start creates tokenized read-only
  routes; the parser bounds total request size/time and rejects bodies and
  ambiguous requests. Snapshot publication serializes an immutable pair, checks
  matching revisions, and coalesces pending snapshots. Slow clients are bounded
  and chart data precedes its associated transport data.
- MSI source policy specifies all-users x64 installation, a Program Files
  destination, stable UpgradeCode, downgrade detection before launch conditions,
  and removal of the previous product inside the installation transaction. The
  artifact verifier inspects final MSI tables and required payloads. These are
  source-review conclusions, not proof that an installed MSI behaves correctly.
  The 2026-10-01 re-audit compared the overrides with the installed cx_Freeze
  8.7.1 implementation and found no further demonstrated policy fault.
- No implementation change or reviewed source result grants release/PR gate
  approval by itself.

## Post-review evidence — 2026-10-01

A7 independently checked the public GitHub run and job-step results for commit
`ca468151485e6479459d1d065e8b4864f585c723`, and read the local frozen smoke and
final MSI evidence. No full test suite was rerun during this documentation-only
update.

- [CI run 36850683918](https://github.com/JoeyZhuoer/ChordCue/actions/runs/36850683918)
  completed successfully at `2026-10-01T10:46:34Z`; its head SHA matches the
  commit above. Both platform jobs concluded `success`.
- The [macOS job](https://github.com/JoeyZhuoer/ChordCue/actions/runs/36850683918/job/110331329670)
  successfully built the original application, compiled the authoritative Swift
  oracle, and compared the Windows core with actual Swift results. The former
  missing macOS/Swift evidence is therefore **closed by CI**; this does not
  establish Logic Pro or physical audio behavior.
- The [Windows job](https://github.com/JoeyZhuoer/ChordCue/actions/runs/36850683918/job/110331329389)
  successfully built the frozen application and MSI, checked final payloads and
  tables, ran the actual frozen GUI/WebEngine/PDF smoke, and passed the final
  installer pytest audit. The former missing final-build/artifact evidence is
  therefore **closed for build, smoke and static artifact checks**. A hosted
  Windows Server runner does not establish consumer-Windows installation
  lifecycle behavior.
- Local `windows/smoke-results/smoke.json` and
  `windows/.diagnostics/frozen-isolated/smoke.json` both record `passed: true`,
  `frozen: true`, PDF creation and window capture on Windows build 19045. Both
  explicitly record physical audio and cross-device LAN as unverified.
- A7 recomputed the retained local candidate identity: `0.2.0`,
  `windows/dist/ChordCue-0.2.0-win-x64.msi`, **141545472 bytes**, SHA-256
  `0fd80c9b0ac5c75966d2060f3f9d7452ef4386b1063626b4fb33215121c69510`.
  Read-only `verify_msi(read_msi(...))` and `verify_tree(...)` passed; actual MSI
  tables exactly match `windows/dist/msi-tables.json`, with **412 File rows**.
  A6's separate completed audit also confirmed all 412 file paths/sizes against
  the frozen tree and checked license hashes, with no findings. This is the
  local candidate's hash; no byte-identity claim is made for the independently
  rebuilt CI MSI.

The user confirmed that additional real-device resources are not currently
available and requested retention of the candidate package with pending
acceptance items. **Keep the candidate and evidence; do not create a PR.**

## Remaining gate evidence

After the post-review evidence above, the following remain
**BLOCKED / NOT ESTABLISHED**:

1. Clean Windows 10 22H2 and Windows 11 x64 install, standard-user runtime,
   repair, upgrade, failed-upgrade rollback, downgrade rejection and uninstall.
   Include a second account, absence of Python, configuration isolation and
   preservation of user data. Neither developer-host smoke nor CI replaces this.
2. Real LAN devices/browsers, network changes and reconnection on the target
   network. Loopback protocol tests cannot substitute for those devices.
3. The required actual-output audio runs: 10 minutes minimized and 10 minutes
   switching tabs, plus output-device changes, lock/sleep/wake behavior and
   measured cross-device timing. Fake/offline synthesis and Qt bridge tests
   cannot establish acoustic performance.

The full outstanding release checklist, including final corresponding-source
and licensing review, is maintained in [VERIFICATION.md](VERIFICATION.md).

Do not create or describe a PR as gate-approved until the agreed real-device and
platform evidence is complete. Keep unsupported or unmeasured results explicit.
