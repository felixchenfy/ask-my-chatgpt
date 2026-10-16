[CmdletBinding()]
param(
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pluginCreatorRoot = Join-Path $env:USERPROFILE ".codex\skills\.system\plugin-creator"
$readMarketplaceName = Join-Path $pluginCreatorRoot "scripts\read_marketplace_name.py"
$updateCachebuster = Join-Path $pluginCreatorRoot "scripts\update_plugin_cachebuster.py"
$validatePlugin = Join-Path $PSScriptRoot "validate-plugin.ps1"
$repairCache = Join-Path $PSScriptRoot "repair-installed-cache.ps1"

foreach ($requiredPath in @($readMarketplaceName, $updateCachebuster, $validatePlugin, $repairCache)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required update path was not found: $requiredPath"
    }
}

$pythonCommand = Get-Command "python.exe" -ErrorAction Stop
$codexCommand = Get-Command "codex.exe" -ErrorAction Stop

Write-Host "Validating the source plugin before reinstall..."
& $validatePlugin -SkipTests:$SkipTests

$marketplaceOutput = @(& $pythonCommand.Source $readMarketplaceName)
if ($LASTEXITCODE -ne 0 -or $marketplaceOutput.Count -eq 0) {
    throw "Could not read the personal marketplace name."
}
$marketplaceName = $marketplaceOutput[-1].Trim()
if (-not $marketplaceName) {
    throw "The personal marketplace name was empty."
}

Write-Host "Refreshing the plugin cachebuster for marketplace '$marketplaceName'..."
& $pythonCommand.Source $updateCachebuster $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Cachebuster update failed with exit code $LASTEXITCODE."
}

Write-Host "Revalidating the updated manifest..."
& $validatePlugin -SkipTests

Write-Host "Reinstalling chatgpt-pdf-review@$marketplaceName..."
& (Join-Path $PSScriptRoot 'prepare-companion.ps1')
& $codexCommand.Source plugin add "chatgpt-pdf-review@$marketplaceName"
if ($LASTEXITCODE -ne 0) {
    throw "Plugin reinstall failed with exit code $LASTEXITCODE."
}

# Codex can retain an emptied version directory while an already-open task still
# holds paths into it. Restore only missing compatibility entrypoints so those
# tasks can start the stable source launcher without overwriting intact caches.
& $repairCache

$manifest = Get-Content -LiteralPath (Join-Path $projectRoot ".codex-plugin\plugin.json") -Raw | ConvertFrom-Json
Write-Host "Installed chatgpt-pdf-review version $($manifest.version) from marketplace '$marketplaceName'."
