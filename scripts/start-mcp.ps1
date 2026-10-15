$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Project environment is missing. Run scripts\setup.ps1 first.'
}
Push-Location $ProjectRoot
try {
    Write-Host 'Starting WebContentAgent MCP over stdio (Ctrl+C to stop)...'
    & $PythonPath -m web_content_agent.mcp.server
    if ($LASTEXITCODE -ne 0) { throw "MCP server failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

