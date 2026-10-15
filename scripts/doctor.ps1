$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UvPath = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvPath) { $UvPath = Join-Path $env:APPDATA 'Python\Python313\Scripts\uv.exe' }
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$(Split-Path $UvPath);$env:Path"
Push-Location $ProjectRoot
try {
    & $UvPath run wca doctor
    if ($LASTEXITCODE -ne 0) { throw "Doctor failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

