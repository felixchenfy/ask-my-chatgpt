$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = Join-Path $ProjectRoot 'scripts\start-onepoint3acres-app.ps1'
if (-not (Test-Path -LiteralPath $Launcher)) { throw "Launcher is missing: $Launcher" }

$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutName = '1Point3Acres Daily Assistant.lnk'
$ShortcutPath = Join-Path $Desktop $ShortcutName
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',14'
$Shortcut.Description = '1Point3Acres full-auto daily assistant using signed-in Chrome'
$Shortcut.Save()

Write-Output $ShortcutPath
