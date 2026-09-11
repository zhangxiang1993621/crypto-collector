param(
    [Parameter(Mandatory = $true)][string]$Base,
    [Parameter(Mandatory = $true)][string]$Head,
    [string]$OutFile
)
# DSH/Windows PowerShell port of review-package (bash original kept alongside).
# Generate a review package: commit list, stat summary, and the net diff with
# extended context, written to a file the reviewer reads in one call.
# Usage: review-package.ps1 -Base BASE -Head HEAD [-OutFile FILE]
$ErrorActionPreference = "Stop"
git rev-parse --verify --quiet $Base *> $null
if ($LASTEXITCODE -ne 0) { Write-Error ("bad BASE: " + $Base); exit 2 }
git rev-parse --verify --quiet $Head *> $null
if ($LASTEXITCODE -ne 0) { Write-Error ("bad HEAD: " + $Head); exit 2 }

if (-not $OutFile) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ws = (& (Join-Path $scriptDir "sdd-workspace.ps1") | Out-String).Trim()
    $b7 = (git rev-parse --short $Base | Out-String).Trim()
    $h7 = (git rev-parse --short $Head | Out-String).Trim()
    $OutFile = Join-Path $ws ("review-" + $b7 + ".." + $h7 + ".diff")
}

$range = $Base + ".." + $Head
$commits = @(git log --oneline $range)
$stat = @(git diff --stat $range)
$diff = @(git diff -U10 $range)
$lines = @("# Review package: " + $range, "") + @("## Commits") + $commits + @("", "## Files changed") + $stat + @("", "## Diff") + $diff
Set-Content -LiteralPath $OutFile -Value $lines -Encoding utf8
$count = (git rev-list --count $range | Out-String).Trim()
$bytes = (Get-Item -LiteralPath $OutFile).Length
Write-Output ("wrote " + $OutFile + ": " + $count + " commit(s), " + $bytes + " bytes")
