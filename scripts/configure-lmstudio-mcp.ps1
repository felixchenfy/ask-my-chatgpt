param([switch]$Disable)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Project environment is missing. Run scripts\setup.ps1 first.'
}
$Arguments = @('-m', 'web_content_agent.mcp.configure')
if ($Disable) { $Arguments += '--disable' }
Push-Location $ProjectRoot
try {
    & $PythonPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "MCP configuration failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

