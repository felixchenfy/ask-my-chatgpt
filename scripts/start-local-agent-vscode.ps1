param(
    [string]$ProjectPath,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
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

Write-Host '[3/5] Checking the localhost LM Studio server and shared model...'
& $LmsPath server start --port 1234 --bind 127.0.0.1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to start LM Studio on 127.0.0.1:1234.' }

$LoadedModels = @(& $LmsPath ps --json | ConvertFrom-Json)
$Loaded = $LoadedModels | Where-Object {
    $_.identifier -eq 'local-agent-vlm' -and
    $_.quantization.name -eq 'Q6_K' -and
    $_.contextLength -eq 32768 -and
    $_.parallel -eq 1 -and
    $_.ttlMs -eq 300000 -and
    $_.vision -eq $true
} | Select-Object -First 1
if (-not $Loaded -or $LoadedModels.Count -ne 1) {
    Write-Host 'Normalizing LM Studio to the single local-agent-vlm instance...'
    & (Join-Path $PSScriptRoot 'load-model.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to load the pinned local model.' }
}

Write-Host '[4/5] Validating the global OpenCode model configuration...'
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
if (
    $Resolved.default_agent -ne 'build' -or
    $Resolved.permission.edit -ne 'allow' -or
    $Resolved.permission.external_directory -ne 'ask'
) {
    throw 'OpenCode global coding permissions do not match the verified build/allow-edit/ask-external policy.'
}

Write-Host '[5/5] Local coding agent ready.'
Write-Host "OpenCode $OpenCodeVersion will use lmstudio/local-agent-vlm in any project."
Write-Host 'Coding policy: build agent, project edits allowed, external-directory access asks first.'
if ($NoLaunch) {
    Write-Host 'Validation-only mode: VS Code was not opened.'
    exit 0
}

if (-not $ProjectPath) {
    Add-Type -AssemblyName System.Windows.Forms
    $Picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $Picker.Description = 'Choose the project folder to open with the local coding agent'
    $DefaultProjects = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Projects'
    if (Test-Path -LiteralPath $DefaultProjects) { $Picker.SelectedPath = $DefaultProjects }
    if ($Picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host 'No project selected; nothing was opened.'
        exit 0
    }
    $ProjectPath = $Picker.SelectedPath
}

$ResolvedProject = (Resolve-Path -LiteralPath $ProjectPath).Path
if (-not (Test-Path -LiteralPath $ResolvedProject -PathType Container)) {
    throw "Project folder does not exist: $ResolvedProject"
}

Write-Host "Opening project: $ResolvedProject"
& $CodePath --new-window $ResolvedProject
if ($LASTEXITCODE -ne 0) { throw 'VS Code failed to open the selected project.' }
Write-Host 'In VS Code, press Ctrl+Esc to open/focus OpenCode, or Ctrl+Shift+Esc for a new session.'
