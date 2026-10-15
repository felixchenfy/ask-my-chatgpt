@echo off
chcp 65001 >nul
title OpenCode WebContentAgent for VS Code
echo Starting the local OpenCode agent in VS Code...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-opencode-vscode.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Keep this window open and send the error to Codex.
  pause
  exit /b 1
)
echo.
echo VS Code is ready. This window can be closed.
exit /b 0
