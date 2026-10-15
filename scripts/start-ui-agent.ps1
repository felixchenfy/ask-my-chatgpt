$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LmsPath = (Get-Command lms -ErrorAction SilentlyContinue).Source
if (-not $LmsPath) { $LmsPath = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe' }
if (-not (Test-Path -LiteralPath $LmsPath)) { throw "lms was not found: $LmsPath" }
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$env:Path"
. (Join-Path $PSScriptRoot 'model-settings.ps1')
Set-WcaBionicModelPreferences

# Start Bionic before normalizing the runtime. If Bionic is launched after the
# CLI load, its local-model auto-fit can replace the 32K/parallel-1 instance
# with a 65K/parallel-4 instance. Starting the controller first and loading the
# pinned runtime last makes the shared instance authoritative for both Chat and
# the Worker.
$BionicPath = Join-Path $env:LOCALAPPDATA 'Programs\Bionic\Bionic.exe'
if (Test-Path -LiteralPath $BionicPath) {
    if (-not (Get-Process -Name Bionic -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $BionicPath
        Start-Sleep -Seconds 3
    }
} else {
    Write-Warning "LM Studio desktop app was not found at: $BionicPath"
}

Write-Host '[1/4] Checking LM Studio server...'
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$ServerStatus = (& $LmsPath server status 2>&1 | Out-String)
$ServerStatusExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference
if ($ServerStatusExitCode -ne 0 -or $ServerStatus -notmatch 'running') {
    & $LmsPath server start --port 1234 --bind 127.0.0.1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start the localhost LM Studio server.' }
}

Write-Host '[2/4] Checking local-agent-vlm...'
$LoadedModels = @(& $LmsPath ps --json | ConvertFrom-Json)
$Loaded = $LoadedModels | Where-Object {
    $_.identifier -eq 'local-agent-vlm' -and
    $_.indexedModelIdentifier -eq $WcaConcreteKey -and
    $_.quantization.name -eq 'Q6_K' -and
    $_.contextLength -eq 32768 -and
    $_.parallel -eq 1 -and
    $_.ttlMs -eq 300000 -and
    $_.vision -eq $true
} | Select-Object -First 1
if (-not $Loaded -or $LoadedModels.Count -ne 1) {
    Write-Host 'Normalizing LM Studio to the single pinned uncensored VLM instance...'
    & (Join-Path $PSScriptRoot 'load-model.ps1')
}

Write-Host '[3/4] Configuring WebContentAgent MCP...'
& (Join-Path $PSScriptRoot 'configure-lmstudio-mcp.ps1')

Write-Host '[4/4] Running MCP health checks...'
& (Join-Path $PSScriptRoot 'mcp-doctor.ps1')

Write-Host ''
Write-Host 'Ready. In LM Studio, use the already loaded local-agent-vlm and start chatting.'
