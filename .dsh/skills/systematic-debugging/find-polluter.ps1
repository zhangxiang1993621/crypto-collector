param(
    [Parameter(Mandatory = $true)][string]$PollutionCheck,
    [Parameter(Mandatory = $true)][string]$TestPattern,
    [string]$TestCommand = "npm test"
)
# DSH/Windows PowerShell port of find-polluter.sh (bash original kept alongside).
# Bisection helper: find which test creates unwanted files/state.
# Usage: find-polluter.ps1 -PollutionCheck ".git" -TestPattern "src/**/*.test.ts" [-TestCommand "npm test"]
$ErrorActionPreference = "Continue"
Write-Output ("Searching for test that creates: " + $PollutionCheck)
Write-Output ("Test pattern: " + $TestPattern)
Write-Output ("Test command: " + $TestCommand)
Write-Output ""

$root = (Get-Location).Path
$like = "*" + [System.IO.Path]::DirectorySeparatorChar + ($TestPattern -replace "\*\*/", "" -replace "/", "\")
$files = @(Get-ChildItem -Recurse -File | Where-Object { $_.FullName.Substring($root.Length) -like $like } | Sort-Object FullName)
$total = $files.Count
Write-Output ("Found " + $total + " test files")
Write-Output ""

$count = 0
foreach ($tf in $files) {
    $count = $count + 1
    if (Test-Path -LiteralPath $PollutionCheck) {
        Write-Output ("Pollution already exists before test " + $count + "/" + $total + " - skipping: " + $tf.FullName)
        continue
    }
    Write-Output ("[" + $count + "/" + $total + "] Testing: " + $tf.FullName)
    $parts = @($TestCommand.Split(" "))
    & $parts[0] ($parts[1..($parts.Length - 1)] + $tf.FullName) *> $null
    if (Test-Path -LiteralPath $PollutionCheck) {
        Write-Output ""
        Write-Output "FOUND POLLUTER!"
        Write-Output ("   Test: " + $tf.FullName)
        Write-Output ("   Created: " + $PollutionCheck)
        Get-Item -LiteralPath $PollutionCheck | Select-Object FullName, Length | Format-List | Out-String | Write-Output
        exit 1
    }
}
Write-Output ""
Write-Output "No polluter found - all tests clean!"
exit 0
