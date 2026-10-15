param(
    [Parameter(Mandatory = $true)][string]$RunDir,
    [Parameter(Mandatory = $true)][string]$TaskName,
    [int]$ExpectedItems = 204,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ResolvedRun = (Resolve-Path -LiteralPath $RunDir).Path
$CheckpointPath = Join-Path $ResolvedRun 'checkpoint.json'
$ManifestPath = Join-Path $ResolvedRun 'run-manifest.json'
if (-not (Test-Path -LiteralPath $CheckpointPath)) { throw "Missing checkpoint: $CheckpointPath" }
if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Missing manifest: $ManifestPath" }

$Checkpoint = Get-Content -LiteralPath $CheckpointPath -Raw | ConvertFrom-Json
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$Articles = @($Checkpoint.articles.PSObject.Properties)
$SuccessCount = @($Articles | Where-Object { $_.Value.status -eq 'success' }).Count
$RunId = Split-Path $ResolvedRun -Leaf
$WorkerRunning = @(
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*$RunId*" -and $_.Name -match '^(wca|python|powershell)'
    }
).Count -gt 0
$Complete = (
    $Manifest.status -eq 'success' -and
    $Checkpoint.current_stage -eq 'complete' -and
    -not $Checkpoint.can_resume -and
    $SuccessCount -ge $ExpectedItems
)

$Record = [ordered]@{
    timestamp = [DateTimeOffset]::Now.ToString('o')
    run_id = $RunId
    worker_running = $WorkerRunning
    manifest_status = $Manifest.status
    stage = $Checkpoint.current_stage
    success = $SuccessCount
    expected = $ExpectedItems
    last_error = $Checkpoint.last_error
    complete = $Complete
}
$LogPath = Join-Path $ProjectRoot "runtime\logs\monitor-$RunId.jsonl"
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
($Record | ConvertTo-Json -Compress -Depth 8) | Add-Content -LiteralPath $LogPath -Encoding utf8

if ($Complete -and -not $DryRun) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
}

$Record | ConvertTo-Json -Depth 8
