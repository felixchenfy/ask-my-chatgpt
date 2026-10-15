$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Project environment is missing. Run scripts\setup.ps1 first.'
}
& $PythonPath (Join-Path $PSScriptRoot 'test_model_tools.py') --count 10
if ($LASTEXITCODE -ne 0) { throw "Tool-call test failed with exit code $LASTEXITCODE" }
