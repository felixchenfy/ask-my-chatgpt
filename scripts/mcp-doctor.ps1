$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$env:Path = "$env:USERPROFILE\.lmstudio\bin;$env:Path"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Project environment is missing. Run scripts\setup.ps1 first.'
}
Push-Location $ProjectRoot
try {
    & $PythonPath -m web_content_agent.mcp.doctor
    if ($LASTEXITCODE -ne 0) { throw "MCP doctor failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

