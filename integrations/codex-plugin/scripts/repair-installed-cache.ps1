[CmdletBinding()]
param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$CacheRoot = (Join-Path $env:USERPROFILE ".codex\plugins\cache\personal\chatgpt-pdf-review")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedSourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$sourceSkillRoot = Join-Path $resolvedSourceRoot "skills\chatgpt-pdf-review"
$sourceMcp = Join-Path $resolvedSourceRoot ".mcp.json"
$sourceLauncher = Join-Path $resolvedSourceRoot "scripts\launch-mcp.cmd"
$sourceAdapter = Join-Path $resolvedSourceRoot "scripts\ask-chatgpt-adapter.py"
$sourceServer = Join-Path $resolvedSourceRoot "server.mjs"
$sourceAgentProcess = Join-Path $resolvedSourceRoot "src\agent-process.mjs"
$sourceAgentConfig = Join-Path $resolvedSourceRoot "src\agent-config.mjs"
$sourceManifest = Join-Path $resolvedSourceRoot ".codex-plugin\plugin.json"

foreach ($requiredPath in @(
    $sourceSkillRoot,
    $sourceMcp,
    $sourceLauncher,
    $sourceAdapter,
    $sourceServer,
    $sourceAgentProcess,
    $sourceAgentConfig,
    $sourceManifest
)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required plugin source path was not found: $requiredPath"
    }
}

if (-not (Test-Path -LiteralPath $CacheRoot)) {
    Write-Host "No installed cache root exists yet: $CacheRoot"
    return
}

function Copy-MissingFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        return $false
    }

    $destinationParent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
    return $true
}

$versionDirectories = @(Get-ChildItem -LiteralPath $CacheRoot -Directory -ErrorAction Stop)
foreach ($versionDirectory in $versionDirectories) {
    $repaired = New-Object System.Collections.Generic.List[string]

    $skillFiles = @(Get-ChildItem -LiteralPath $sourceSkillRoot -File -Recurse)
    foreach ($sourceFile in $skillFiles) {
        $relativePath = $sourceFile.FullName.Substring($sourceSkillRoot.Length).TrimStart('\')
        $destination = Join-Path (Join-Path $versionDirectory.FullName "skills\chatgpt-pdf-review") $relativePath
        if (Copy-MissingFile -Source $sourceFile.FullName -Destination $destination) {
            $repaired.Add("skills\chatgpt-pdf-review\$relativePath")
        }
    }

    $compatibilityFiles = @(
        @{ Source = $sourceMcp; Relative = ".mcp.json" },
        @{ Source = $sourceLauncher; Relative = "scripts\launch-mcp.cmd" },
        @{ Source = $sourceAdapter; Relative = "scripts\ask-chatgpt-adapter.py" },
        @{ Source = $sourceServer; Relative = "server.mjs" },
        @{ Source = $sourceAgentProcess; Relative = "src\agent-process.mjs" },
        @{ Source = $sourceAgentConfig; Relative = "src\agent-config.mjs" },
        @{ Source = $sourceManifest; Relative = ".codex-plugin\plugin.json" }
    )
    foreach ($compatibilityFile in $compatibilityFiles) {
        $destination = Join-Path $versionDirectory.FullName $compatibilityFile.Relative
        if (Copy-MissingFile -Source $compatibilityFile.Source -Destination $destination) {
            $repaired.Add($compatibilityFile.Relative)
        }
    }

    if ($repaired.Count -gt 0) {
        Write-Host "Repaired cached plugin $($versionDirectory.Name): $($repaired -join ', ')"
    }
    else {
        Write-Host "Cached plugin $($versionDirectory.Name) already has all compatibility entrypoints."
    }
}
