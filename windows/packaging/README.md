# Windows packaging

Use CPython 3.13 x64 and the repository's hashed lock. From the repository root:

```powershell
py -3.13 -m venv windows/.venv
windows/.venv/Scripts/python -m pip install --require-hashes -r windows/requirements.lock
windows/.venv/Scripts/python -m pip install --no-deps --no-build-isolation -e windows
windows/scripts/build.ps1
windows/scripts/smoke.ps1
```

`build.ps1` clears only generated `exe`, `licenses`, and `msi` children of
`windows/build`, runs cx_Freeze 8.7.1 `build_exe` then `bdist_msi`, and audits the
finished payload and MSI tables. Outputs are `windows/dist/*.msi` and
`windows/dist/msi-tables.json`. It never installs or launches the MSI.

For the independent Qt/WebEngine proof before application integration:

```powershell
windows/scripts/build.ps1 -Prototype
windows/scripts/smoke.ps1 -Prototype -OutputDirectory windows/smoke-prototype
```

The probe creates a Qt window, renders an oscillator with **OfflineAudioContext**
(no audible output), and exports a PDF. Passing this probe confirms frozen
dependencies load on the test host. It does not measure sound-device latency,
browser synchronization, suspend/resume, or consumer Windows compatibility.

## Installer policy

- 64-bit Windows 10 22H2 (build 19045) or later; Windows 11 satisfies this check.
  A standard 64-bit registry search reads the build because MSI's `VersionNT64`
  can report 603 on Windows 10. Maintenance remains available via `Installed`.
- Per-machine `ALLUSERS=1`, default `[ProgramFiles64Folder]\ChordCue`.
- UpgradeCode `{59A75696-D601-43F8-B523-985CF1C78E60}` is permanent across releases.
  ProductCode is deterministic per version. Increment the three-field release
  version for each distributed build; do not publish changed payloads under the
  same version/ProductCode.
- Newer products are **detect-only** and cause a LaunchCondition failure.
  `FindRelatedProducts` and `AppSearch` run before LaunchConditions in both UI
  and execute sequences, so silent installs receive the same checks.
- `RemoveExistingProducts` runs at 1501, after `InstallInitialize` (1500) and
  before `ProcessComponents` (1600). This is the early transactional major-upgrade
  strategy: old removal is rolled back when replacement fails. cx_Freeze's
  generated component GUIDs are not suitable for switching to late removal.
- No install-time app launch, PATH/environment changes, firewall rule or
  application registry custom action, or AppData removal.
- Qt plugins, WebEngine subprocess/resources/locales, Python, application
  Resources, available dependency licenses, and app-local VC runtime are bundled.
  VC redistribution terms are included under `share/licenses/vc_redist`.
  Windows system `icuuc.dll` is deliberately excluded from bundle resolution;
  unrelated ICU builds from developer PATH are incompatible with Qt's Windows ICU.

The MSI verifier reads the actual database (Property, Upgrade, sequences,
LaunchCondition, AppSearch/RegLocator, actions, directories and files). Optional
pytest artifact checks use `CHORDCUE_MSI` and `CHORDCUE_FROZEN_DIR`.

## Elevated lifecycle acceptance

On a **disposable** Windows VM with no existing ChordCue installation, open an
elevated terminal yourself. This harness never requests or automates UAC:

```powershell
windows/.venv/Scripts/python windows/scripts/test_msi_lifecycle.py `
  path/to/older-release.msi path/to/newer-release.msi `
  --output windows/lifecycle-results --allow-machine-changes
```

The harness checks install, failed-upgrade rollback restoring the old executable
and product registration, successful upgrade, repair of a missing executable,
blocked downgrade, uninstall and preservation of an AppData sentinel. It retains
verbose MSI logs and a JSON report. Its injected-failure MSI is clearly marked
**TEST-ONLY-DO-NOT-DISTRIBUTE**; never publish it as a release artifact.

Release acceptance still requires clean Windows 10 and 11 machines without
Python, install/update/repair/uninstall logs, non-admin app use, CJK and spaces in
user paths, WebEngine/PDF/audio/LAN checks, and two physical devices for browser
audio. GitHub's Windows Server runner and table inspection do not satisfy those
real-machine acceptance gates. Code-signing must be recorded separately; this
build does not claim a signed or SmartScreen-trusted release.

References: [cx_Freeze MSI options](https://cx-freeze.readthedocs.io/en/8.7.1/bdist_msi.html),
[Windows Installer removal sequencing](https://learn.microsoft.com/en-us/windows/win32/msi/removeexistingproducts-action),
[VersionNT compatibility](https://learn.microsoft.com/en-us/troubleshoot/windows-client/application-management/versionnt-value-for-windows-10-server).
