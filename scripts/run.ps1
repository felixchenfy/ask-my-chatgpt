param(
    [Parameter(Mandatory = $true)][string]$Config,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ConfigPath = (Resolve-Path -LiteralPath $Config).Path
$UvPath = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvPath) { $UvPath = Join-Path $env:APPDATA 'Python\Python313\Scripts\uv.exe' }
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$(Split-Path $UvPath);$env:Path"
Push-Location $ProjectRoot
try {
    $Arguments = @('run', 'wca', 'run', $ConfigPath)
    if ($Force) { $Arguments += '--force' }
    & $UvPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Worker failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

