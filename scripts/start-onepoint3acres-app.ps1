$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UvPath = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvPath) { $UvPath = Join-Path $env:APPDATA 'Python\Python313\Scripts\uv.exe' }
if (-not (Test-Path -LiteralPath $UvPath)) { throw 'uv is not installed or could not be found.' }

Push-Location $ProjectRoot
try {
    & $UvPath run wca-onepoint3acres-app
    if ($LASTEXITCODE -ne 0) { throw "App exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
