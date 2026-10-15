$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    $UvPath = Join-Path $env:APPDATA 'Python\Python313\Scripts\uv.exe'
    if (-not (Test-Path -LiteralPath $UvPath)) {
        throw "uv is not installed. Install with: py -3.13 -m pip install --user uv"
    }
    $Uv = Get-Item -LiteralPath $UvPath
}
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$($Uv.DirectoryName);$env:Path"
Push-Location $ProjectRoot
try {
    & $Uv.FullName sync --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }
    & $Uv.FullName run playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium install failed with exit code $LASTEXITCODE" }
    Write-Host "Setup complete: $ProjectRoot"
} finally {
    Pop-Location
}

