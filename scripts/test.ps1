$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$UvPath = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvPath) { $UvPath = Join-Path $env:APPDATA 'Python\Python313\Scripts\uv.exe' }
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$(Split-Path $UvPath);$env:Path"
Push-Location $ProjectRoot
try {
    & $UvPath run ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed with exit code $LASTEXITCODE" }
    & $UvPath run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed with exit code $LASTEXITCODE" }
    & $UvPath run pytest
    if ($LASTEXITCODE -ne 0) { throw "Pytest failed with exit code $LASTEXITCODE" }
    & node --test tests/js/*.test.mjs
    if ($LASTEXITCODE -ne 0) { throw "Chrome companion tests failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
