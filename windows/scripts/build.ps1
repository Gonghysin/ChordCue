param(
    [switch]$Prototype,
    [switch]$SkipMsi,
    [string]$Python = ""
)
$ErrorActionPreference = 'Stop'
$WindowsRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $WindowsRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Create windows/.venv and install requirements.lock first.' }
$BuildRoot = Join-Path $WindowsRoot $(if ($Prototype) { 'build-prototype' } else { 'build' })
$DistRoot = Join-Path $WindowsRoot $(if ($Prototype) { 'dist-prototype' } else { 'dist' })
$PreviousPrototype = $env:CHORDCUE_BUILD_PROTOTYPE
try {
    # cx_Freeze does not remove obsolete payloads. Only clear these generated
    # child directories after checking their absolute paths stay in our build.
    $ExpectedBuild = [IO.Path]::GetFullPath($BuildRoot).TrimEnd('\') + '\'
    foreach ($Leaf in @('exe', 'licenses', 'msi')) {
        $Generated = [IO.Path]::GetFullPath((Join-Path $BuildRoot $Leaf))
        if (-not $Generated.StartsWith($ExpectedBuild, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove path outside generated build: $Generated"
        }
        if (Test-Path -LiteralPath $Generated) { Remove-Item -LiteralPath $Generated -Recurse -Force }
    }
    $env:CHORDCUE_BUILD_PROTOTYPE = $(if ($Prototype) { '1' } else { '0' })
    & $Python (Join-Path $WindowsRoot 'packaging/make_icon.py') (Join-Path $BuildRoot 'ChordCue.ico')
    if ($LASTEXITCODE) { throw 'Application icon generation failed' }
    & $Python (Join-Path $WindowsRoot 'packaging/collect_licenses.py') (Join-Path $BuildRoot 'licenses')
    if ($LASTEXITCODE) { throw 'License collection failed' }
    & $Python (Join-Path $WindowsRoot 'packaging/setup.py') build_exe
    if ($LASTEXITCODE) { throw 'cx_Freeze build_exe failed' }
    & $Python (Join-Path $WindowsRoot 'packaging/verify_artifacts.py') --tree (Join-Path $BuildRoot 'exe')
    if ($LASTEXITCODE) { throw 'Frozen payload audit failed' }
    if (-not $SkipMsi -and -not $Prototype) {
        & $Python (Join-Path $WindowsRoot 'packaging/setup.py') bdist_msi --skip-build
        if ($LASTEXITCODE) { throw 'cx_Freeze bdist_msi failed' }
        $Packages = @(Get-ChildItem -LiteralPath $DistRoot -Filter '*.msi')
        if ($Packages.Count -ne 1) { throw 'Expected exactly one MSI in dist. Use a clean build workspace.' }
        & $Python (Join-Path $WindowsRoot 'packaging/verify_artifacts.py') --msi $Packages[0].FullName --report (Join-Path $DistRoot 'msi-tables.json')
        if ($LASTEXITCODE) { throw 'Final MSI table audit failed' }
        Get-FileHash -Algorithm SHA256 -LiteralPath $Packages[0].FullName | Format-List
    }
} finally {
    $env:CHORDCUE_BUILD_PROTOTYPE = $PreviousPrototype
}
