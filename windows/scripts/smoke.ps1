param(
    [switch]$Prototype,
    [string]$Exe = '',
    [string]$OutputDirectory = '',
    [string]$ScoreFile = ''
)
$ErrorActionPreference = 'Stop'
$WindowsRoot = Split-Path -Parent $PSScriptRoot
if (-not $Exe) {
    $Exe = Join-Path $WindowsRoot $(if ($Prototype) { 'build-prototype/exe/ChordCueProbe.exe' } else { 'build/exe/ChordCue.exe' })
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $WindowsRoot 'smoke-results' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$StartedAt = Get-Date
$QuotedOutput = '"{0}"' -f $OutputDirectory
$Arguments = if ($Prototype) { @($QuotedOutput) } else { @('--smoke-test', $QuotedOutput) }
if ($ScoreFile -and -not $Prototype) {
    $Arguments += @('--smoke-score', ('"{0}"' -f [IO.Path]::GetFullPath($ScoreFile)))
}
$Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $OutputDirectory 'stdout.log') -RedirectStandardError (Join-Path $OutputDirectory 'stderr.log')
if (-not $Process.WaitForExit(45000)) {
    Stop-Process -Id $Process.Id -Force
    throw 'Frozen application smoke test timed out'
}
if ($Process.ExitCode -ne 0) { throw "Frozen smoke exited $($Process.ExitCode); inspect $OutputDirectory" }
$ReportName = if ($Prototype) { 'probe.json' } else { 'smoke.json' }
$Report = Join-Path $OutputDirectory $ReportName
if (-not (Test-Path -LiteralPath $Report)) { throw "Missing smoke result: $Report" }
if ((Get-Item -LiteralPath $Report).LastWriteTime -lt $StartedAt) { throw 'Smoke report was not updated by this run' }
$Result = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
if (-not $Result.frozen) { throw 'Smoke result did not come from a frozen executable' }
if (-not $Prototype -and -not $Result.passed) { throw 'Full application smoke report failed' }
Get-Content -LiteralPath $Report
