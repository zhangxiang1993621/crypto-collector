param(
    [string]$ProjectRoot = (Get-Location).Path
)

# validate-install.ps1 - Superpowers for DSH 安装校验器
# 对标原 .trae/hooks/validate-package.ps1，按 DSH 布局重写。
# 用法: pwsh -NoProfile -File .dsh/scripts/validate-install.ps1 [-ProjectRoot <path>]

$ErrorActionPreference = "Stop"
$failures = New-Object System.Collections.ArrayList
$warnings = New-Object System.Collections.ArrayList

function Add-Failure { param([string]$Message) [void]$failures.Add($Message) }
function Add-Warning { param([string]$Message) [void]$warnings.Add($Message) }

$ExpectedSkills = @(
    "brainstorming", "collision-zone-thinking", "condition-based-waiting",
    "defense-in-depth", "dispatching-parallel-agents", "executing-plans",
    "finishing-a-development-branch", "inversion-exercise", "meta-pattern-recognition",
    "preserving-productive-tensions", "receiving-code-review", "requesting-code-review",
    "root-cause-tracing", "scale-game", "simplification-cascades",
    "subagent-driven-development", "systematic-debugging",
    "test-driven-development", "testing-anti-patterns", "testing-skills-with-subagents",
    "tracing-knowledge-lineages", "using-git-worktrees", "using-superpowers",
    "verification-before-completion", "when-stuck", "writing-plans", "writing-skills"
)
$ExpectedAgents = @(
    "superpowers-implementer.md", "superpowers-task-reviewer.md",
    "superpowers-code-reviewer.md", "superpowers-plan-reviewer.md"
)
$BeginMarker = "<!-- BEGIN superpowers-dsh -->"
$EndMarker = "<!-- END superpowers-dsh -->"

$skillsRoot = Join-Path $ProjectRoot ".dsh/skills"
$agentsRoot = Join-Path $ProjectRoot ".dsh/agents"
$agentsMd = Join-Path $ProjectRoot "AGENTS.md"
$envJson = Join-Path $ProjectRoot ".dsh/env.json"
$requests = Join-Path $ProjectRoot ".dsh/REQUESTS.md"
$selfScript = Join-Path $ProjectRoot ".dsh/scripts/validate-install.ps1"

Write-Output ("ProjectRoot: " + $ProjectRoot)
Write-Output ""

# --- 1. skills ---
if (-not (Test-Path -LiteralPath $skillsRoot)) {
    Add-Failure "missing directory .dsh/skills"
}
else {
    foreach ($loose in @(Get-ChildItem -LiteralPath $skillsRoot -File)) {
        Add-Failure ("loose file at .dsh/skills root: " + $loose.Name + " (DSH parses top-level *.md as flat skills)")
    }
    foreach ($name in $ExpectedSkills) {
        $sk = Join-Path $skillsRoot ($name + "/SKILL.md")
        if (-not (Test-Path -LiteralPath $sk)) {
            Add-Failure ("missing skill: " + $name)
            continue
        }
        $head = @(Get-Content -LiteralPath $sk -TotalCount 40 -Encoding UTF8)
        if ($head.Count -lt 3 -or $head[0].TrimEnd() -ne "---") {
            Add-Failure ($name + ": SKILL.md has no YAML frontmatter")
            continue
        }
        $fmEnd = -1
        for ($i = 1; $i -lt $head.Count; $i++) {
            if ($head[$i].TrimEnd() -eq "---") { $fmEnd = $i; break }
        }
        if ($fmEnd -lt 0) {
            Add-Failure ($name + ": unterminated frontmatter")
            continue
        }
        $hasName = $false; $hasDesc = $false
        foreach ($fl in $head[1..($fmEnd - 1)]) {
            if ($fl -match '^name:s*(.+)$') {
                $hasName = $true
                $val = $Matches[1].Trim().Trim('"').Trim([char]39)
                if ($val -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') {
                    Add-Failure ($name + ": frontmatter name is not kebab-case: " + $val)
                }
                elseif ($val -ne $name) {
                    Add-Warning ($name + ": frontmatter name differs from directory name: " + $val)
                }
            }
            elseif ($fl -match '^description:s*(.*)$') {
                $hasDesc = $true
                if ($Matches[1].Trim().Length -eq 0) { Add-Warning ($name + ": description may be empty (check folded YAML)") }
            }
            elseif ($fl -match '^when_to_use:') { Add-Failure ($name + ": old Trae key when_to_use (DSH uses whenToUse)") }
            elseif ($fl -match '^version:') { Add-Failure ($name + ": unsupported key version (remove or move to metadata)") }
        }
        if (-not $hasName) { Add-Failure ($name + ": frontmatter missing name") }
        if (-not $hasDesc) { Add-Failure ($name + ": frontmatter missing description") }
    }
    $extra = @(Get-ChildItem -LiteralPath $skillsRoot -Directory | Where-Object { $ExpectedSkills -notcontains $_.Name })
    foreach ($e in $extra) { Add-Warning ("unexpected skill directory (not from this suite): " + $e.Name) }
}

# --- 2. agents ---
if (-not (Test-Path -LiteralPath $agentsRoot)) {
    Add-Failure "missing directory .dsh/agents"
}
else {
    foreach ($a in $ExpectedAgents) {
        if (-not (Test-Path -LiteralPath (Join-Path $agentsRoot $a))) { Add-Failure ("missing agent template: " + $a) }
    }
}

# --- 3. AGENTS.md marker section ---
if (-not (Test-Path -LiteralPath $agentsMd)) {
    Add-Failure "missing AGENTS.md at project root"
}
else {
    $text = Get-Content -LiteralPath $agentsMd -Raw -Encoding UTF8
    $beginCount = ([regex]::Matches($text, [regex]::Escape($BeginMarker))).Count
    $endCount = ([regex]::Matches($text, [regex]::Escape($EndMarker))).Count
    if ($beginCount -ne 1 -or $endCount -ne 1) {
        Add-Failure ("AGENTS.md markers: found " + $beginCount + " BEGIN and " + $endCount + " END (expected exactly 1 each)")
    }
    else {
        $b = $text.IndexOf($BeginMarker)
        $e = $text.IndexOf($EndMarker)
        if ($e -le $b) { Add-Failure "AGENTS.md: END marker appears before BEGIN marker" }
        elseif (($e - $b) -lt 500) { Add-Failure "AGENTS.md: marker section is suspiciously short" }
        else { Write-Output ("AGENTS.md marker section OK (" + ($e - $b) + " chars)") }
    }
}

# --- 4. aux files ---
if (-not (Test-Path -LiteralPath $envJson)) {
    Add-Warning "missing .dsh/env.json (Python rule will ask for env on first use)"
}
else {
    try {
        $j = Get-Content -LiteralPath $envJson -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not ($j.PSObject.Properties.Name -contains "python_env")) { Add-Failure ".dsh/env.json has no python_env key" }
        elseif ([string]::IsNullOrWhiteSpace($j.python_env)) { Add-Warning ".dsh/env.json python_env is empty (fill on first Python use)" }
    }
    catch { Add-Failure (".dsh/env.json is not valid JSON: " + $_.Exception.Message) }
}
if (-not (Test-Path -LiteralPath $requests)) { Add-Warning "missing .dsh/REQUESTS.md" }
if (-not (Test-Path -LiteralPath $selfScript)) { Add-Warning "validator itself not installed at .dsh/scripts/validate-install.ps1" }

# --- 5. Windows script ports present ---
$ports = @(
    ".dsh/skills/subagent-driven-development/scripts/sdd-workspace.ps1",
    ".dsh/skills/subagent-driven-development/scripts/review-package.ps1",
    ".dsh/skills/subagent-driven-development/scripts/task-brief.ps1",
    ".dsh/skills/root-cause-tracing/find-polluter.ps1",
    ".dsh/skills/systematic-debugging/find-polluter.ps1"
)
foreach ($p in $ports) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $p))) { Add-Warning ("missing Windows port: " + $p) }
}

# --- report ---
Write-Output ""
foreach ($w in $warnings) { Write-Output ("WARN: " + $w) }
foreach ($f in $failures) { Write-Output ("FAIL: " + $f) }
Write-Output ""
if ($failures.Count -gt 0) {
    Write-Output ("RESULT: FAIL (" + $failures.Count + " failure(s), " + $warnings.Count + " warning(s))")
    exit 1
}
Write-Output ("RESULT: PASS (0 failures, " + $warnings.Count + " warning(s))")
exit 0
