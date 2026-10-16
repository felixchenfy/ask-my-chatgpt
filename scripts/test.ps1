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
    # Codex plugin integration: run its Node tests and ESLint when its dependencies are
    # installed (`npm ci` in integrations/codex-plugin; CI always installs them).
    $PluginRoot = Join-Path $ProjectRoot 'integrations\codex-plugin'
    if (Test-Path -LiteralPath (Join-Path $PluginRoot 'node_modules') -PathType Container) {
        Push-Location $PluginRoot
        try {
            & npm.cmd test
            if ($LASTEXITCODE -ne 0) { throw "Codex plugin tests failed with exit code $LASTEXITCODE" }
            & npm.cmd run lint
            if ($LASTEXITCODE -ne 0) { throw "Codex plugin lint failed with exit code $LASTEXITCODE" }
        } finally { Pop-Location }
    } else {
        Write-Warning "Skipping Codex plugin tests: run 'npm ci' in integrations\codex-plugin first."
    }
} finally { Pop-Location }
