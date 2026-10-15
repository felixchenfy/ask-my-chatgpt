param(
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$WorkspaceFile = Join-Path $ProjectRoot 'WebContentAgent-OpenCode.code-workspace'
$OpenCodeConfig = Join-Path $env:USERPROFILE '.config\opencode\opencode.json'

function Require-Command([string]$Name) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $Command) { throw "$Name is not installed or is not on PATH." }
    return $Command.Source
}

Write-Host '[1/5] Checking VS Code and OpenCode...'
$CodePath = Require-Command 'code'
$OpenCodePath = Require-Command 'opencode'
$LmsPath = (Get-Command lms -ErrorAction SilentlyContinue).Source
if (-not $LmsPath) { $LmsPath = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe' }
if (-not (Test-Path -LiteralPath $LmsPath)) { throw "lms was not found: $LmsPath" }

$OpenCodeVersion = (& $OpenCodePath --version | Out-String).Trim()
if (-not $OpenCodeVersion) { throw 'OpenCode did not return a version.' }

Write-Host '[2/5] Ensuring the official OpenCode VS Code extension...'
$Extensions = @(& $CodePath --list-extensions)
if ($Extensions -notcontains 'sst-dev.opencode') {
    & $CodePath --install-extension sst-dev.opencode
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install sst-dev.opencode.' }
}

Write-Host '[3/5] Checking the localhost LM Studio server and single model...'
& $LmsPath server start --port 1234 --bind 127.0.0.1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to start LM Studio on 127.0.0.1:1234.' }

$LoadedModels = @(& $LmsPath ps --json | ConvertFrom-Json)
$Loaded = $LoadedModels | Where-Object {
    $_.identifier -eq 'local-agent-vlm' -and
    $_.contextLength -eq 32768 -and
    $_.parallel -eq 1 -and
    $_.ttlMs -eq 300000 -and
    $_.vision -eq $true
} | Select-Object -First 1
if (-not $Loaded -or $LoadedModels.Count -ne 1) {
    Write-Host 'Normalizing LM Studio to the one pinned local-agent-vlm instance...'
    & (Join-Path $PSScriptRoot 'load-model.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to load the pinned model.' }
}

Write-Host '[4/5] Validating OpenCode model and MCP configuration...'
if (-not (Test-Path -LiteralPath $OpenCodeConfig)) {
    throw "OpenCode configuration is missing: $OpenCodeConfig"
}
$Resolved = & $OpenCodePath debug config --pure | ConvertFrom-Json
if ($Resolved.model -ne 'lmstudio/local-agent-vlm') {
    throw "Unexpected OpenCode model: $($Resolved.model)"
}
$ModelConfig = $Resolved.provider.lmstudio.models.'local-agent-vlm'
if (
    $Resolved.provider.lmstudio.options.baseURL -ne 'http://127.0.0.1:1234/v1' -or
    $ModelConfig.limit.context -ne 32768 -or
    $ModelConfig.limit.output -ne 4096
) {
    throw 'OpenCode LM Studio limits or base URL do not match the verified configuration.'
}
$McpStatus = (& $OpenCodePath mcp list --pure | Out-String)
if ($LASTEXITCODE -ne 0 -or $McpStatus -notmatch 'web-content-agent' -or $McpStatus -notmatch 'connected') {
    throw 'WebContentAgent MCP did not connect through OpenCode.'
}

Write-Host '[5/5] Ready.'
Write-Host "OpenCode $OpenCodeVersion will use lmstudio/local-agent-vlm."
Write-Host 'LM Studio remains a single 32K, parallel-1 model instance with a 20-minute idle TTL.'
if ($NoLaunch) {
    Write-Host 'Validation-only mode: VS Code was not opened.'
    exit 0
}

Write-Host 'Opening the dedicated VS Code workspace...'
& $CodePath --new-window $WorkspaceFile
if ($LASTEXITCODE -ne 0) { throw 'VS Code failed to open the OpenCode workspace.' }
Write-Host 'First launch only: trust this workspace, then allow automatic tasks.'
Write-Host 'After those one-time approvals, future desktop launches start OpenCode automatically.'
