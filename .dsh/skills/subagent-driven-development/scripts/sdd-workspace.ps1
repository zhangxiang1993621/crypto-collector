# DSH/Windows PowerShell port of sdd-workspace (bash original kept alongside).
# Resolve and ensure the working-tree directory SDD uses for short-lived artifacts:
# task briefs, implementer reports, review packages, progress ledger.
# Prints the directory's absolute path. Falls back to cwd when not inside a git repo.
$ErrorActionPreference = "Stop"
$root = $null
try { $root = (git rev-parse --show-toplevel 2>$null | Out-String).Trim() } catch { $root = $null }
if ([string]::IsNullOrWhiteSpace($root)) { $root = (Get-Location).Path }
$dir = Join-Path $root ".superpowers/sdd"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Set-Content -LiteralPath (Join-Path $dir ".gitignore") -Value "*" -Encoding utf8
Write-Output $dir
