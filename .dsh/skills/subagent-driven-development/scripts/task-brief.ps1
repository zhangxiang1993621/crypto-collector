param(
    [Parameter(Mandatory = $true)][string]$PlanFile,
    [Parameter(Mandatory = $true)][string]$TaskNumber,
    [string]$OutFile
)
# DSH/Windows PowerShell port of task-brief (bash original kept alongside).
# Extract one task's full text from an implementation plan into a file the
# implementer reads in one call.
# Usage: task-brief.ps1 -PlanFile PLAN -TaskNumber N [-OutFile FILE]
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PlanFile)) { Write-Error ("no such plan file: " + $PlanFile); exit 2 }

if (-not $OutFile) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ws = (& (Join-Path $scriptDir "sdd-workspace.ps1") | Out-String).Trim()
    $OutFile = Join-Path $ws ("task-" + $TaskNumber + "-brief.md")
}

$fence = [string][char]96 * 3
$inFence = $false
$inTask = $false
$out = New-Object System.Collections.ArrayList
foreach ($line in @(Get-Content -LiteralPath $PlanFile -Encoding UTF8)) {
    if ($line.StartsWith($fence)) { $inFence = -not $inFence }
    if ((-not $inFence) -and ($line -match '^#+\s+Task\s+(\d+)')) {
        $inTask = ($Matches[1] -eq $TaskNumber)
    }
    if ($inTask) { [void]$out.Add($line) }
}
if ($out.Count -eq 0) {
    Write-Error ("task " + $TaskNumber + " not found in " + $PlanFile + " (no heading matching 'Task " + $TaskNumber + "')")
    exit 3
}
Set-Content -LiteralPath $OutFile -Value $out.ToArray() -Encoding utf8
Write-Output ("wrote " + $OutFile + ": " + $out.Count + " lines")
