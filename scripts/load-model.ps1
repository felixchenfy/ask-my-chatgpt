param(
    [ValidateRange(5, 86400)]
    [int]$IdleTtlSeconds = 300
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LmsPath = (Get-Command lms -ErrorAction SilentlyContinue).Source
if (-not $LmsPath) { $LmsPath = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe' }
if (-not (Test-Path -LiteralPath $LmsPath)) { throw "lms was not found: $LmsPath" }
. (Join-Path $PSScriptRoot 'model-settings.ps1')

# Bionic 1.0.6 Runtime 3 auto-fit ignored explicit context length during this
# machine's audit. Disable only that experimental switch, preserving all other settings.
Set-WcaBionicModelPreferences

& $LmsPath server start --port 1234 --bind 127.0.0.1 | Out-Null
$Models = @(& $LmsPath ls --json | ConvertFrom-Json)
$Q6 = $Models | Where-Object {
    $_.indexedModelIdentifier -eq $WcaConcreteKey -and $_.quantization.name -eq 'Q6_K'
} | Select-Object -First 1
if (-not $Q6) {
    Write-Host 'Downloading the pinned DavidAU uncensored Q6_K VLM (LM Studio manages the model files)...'
    & $LmsPath get $WcaDownloadUrl --gguf --yes
    if ($LASTEXITCODE -ne 0) { throw 'LM Studio failed to download the pinned Q6_K model.' }
    $Models = @(& $LmsPath ls --json | ConvertFrom-Json)
    $Q6 = $Models | Where-Object {
        $_.indexedModelIdentifier -eq $WcaConcreteKey -and $_.quantization.name -eq 'Q6_K'
    } | Select-Object -First 1
    if (-not $Q6) { throw "Downloaded model was not indexed as expected: $WcaConcreteKey" }
}

& $LmsPath unload --all | Out-Null
& $LmsPath load $WcaRuntimeKey --identifier local-agent-vlm --gpu max `
    --context-length 32768 --parallel 1 --ttl $IdleTtlSeconds `
    --no-speculative-draft-mtp --yes
if ($LASTEXITCODE -ne 0) {
    throw "Failed to load $WcaConcreteKey"
}

$LoadedModels = @(& $LmsPath ps --json | ConvertFrom-Json)
$Loaded = $LoadedModels | Where-Object {
    $_.identifier -eq 'local-agent-vlm' -and
    $_.indexedModelIdentifier -eq $WcaConcreteKey -and
    $_.quantization.name -eq 'Q6_K' -and
    $_.contextLength -eq 32768 -and
    $_.parallel -eq 1 -and
    $_.ttlMs -eq ($IdleTtlSeconds * 1000) -and
    $_.vision -eq $true
} | Select-Object -First 1
if (-not $Loaded) { throw 'LM Studio did not report the expected DavidAU Q6_K local-agent-vlm instance.' }
if ($LoadedModels.Count -ne 1) { throw "Expected exactly one loaded model, found $($LoadedModels.Count)." }
Write-Host "Loaded the DavidAU uncensored VLM as the only instance: local-agent-vlm (Q6_K, context 32768, parallel 1, GPU max, idle TTL $IdleTtlSeconds seconds)."
