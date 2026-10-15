$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DesktopPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$ConfigPath = Join-Path $DesktopPath 'WebContent\sites\mit-news\news-demo\source.yaml'
& (Join-Path $PSScriptRoot 'load-model.ps1')
if ($LASTEXITCODE -ne 0) { throw "Model load failed before demo" }
& (Join-Path $PSScriptRoot 'doctor.ps1')
if ($LASTEXITCODE -ne 0) { throw "Doctor failed before demo" }
& (Join-Path $PSScriptRoot 'run.ps1') -Config $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "Demo failed" }
