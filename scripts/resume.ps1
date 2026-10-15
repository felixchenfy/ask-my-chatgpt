param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [switch]$Reclean
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UvPath = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvPath) { $UvPath = Join-Path $env:APPDATA 'Python\Python313\Scripts\uv.exe' }
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$(Split-Path $UvPath);$env:Path"
Push-Location $ProjectRoot
try {
    $Arguments = @('run', 'wca', 'resume', $RunId)
    if ($Reclean) { $Arguments += '--reclean' }
    & $UvPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Resume failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
