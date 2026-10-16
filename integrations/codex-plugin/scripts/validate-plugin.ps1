[CmdletBinding()]
param(
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$systemSkillsRoot = Join-Path $env:USERPROFILE ".codex\skills\.system"
$skillValidator = Join-Path $systemSkillsRoot "skill-creator\scripts\quick_validate.py"
$pluginValidator = Join-Path $systemSkillsRoot "plugin-creator\scripts\validate_plugin.py"
$pluginSkill = Join-Path $projectRoot "skills\chatgpt-pdf-review"
$cacheRepair = Join-Path $projectRoot "scripts\repair-installed-cache.ps1"
$pluginUpdater = Join-Path $projectRoot "scripts\update-installed-plugin.ps1"

$requiredPaths = @(
    $skillValidator,
    $pluginValidator,
    $pluginSkill,
    $cacheRepair,
    $pluginUpdater
)

foreach ($requiredPath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required validation path was not found: $requiredPath"
    }
}

$uvCommand = Get-Command "uv" -ErrorAction Stop

# The bundled validators import PyYAML and call Path.read_text() without an
# explicit encoding. Keep both requirements local to this process so validation
# is reproducible on Windows without modifying the user's system Python.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "Validating Codex skill metadata and instructions..."
& $uvCommand.Source run --with pyyaml python $skillValidator $pluginSkill
if ($LASTEXITCODE -ne 0) {
    throw "Skill validation failed with exit code $LASTEXITCODE."
}

Write-Host "Validating Codex plugin structure..."
& $uvCommand.Source run --with pyyaml python $pluginValidator $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Plugin validation failed with exit code $LASTEXITCODE."
}

if (-not $SkipTests) {
    $npmCommand = Get-Command "npm.cmd" -ErrorAction Stop
    Write-Host "Running project regression tests..."
    Push-Location $projectRoot
    try {
        & $npmCommand.Source test
        if ($LASTEXITCODE -ne 0) {
            throw "Project tests failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "Plugin validation completed successfully."
