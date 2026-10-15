@echo off
chcp 65001 >nul
title Local Coding Agent for VS Code
echo Starting the shared local coding agent...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local-agent-vscode.ps1" %*
if errorlevel 1 (
  echo.
  echo Startup failed. Keep this window open and send the error to Codex.
  pause
  exit /b 1
)
echo.
echo VS Code is ready. Press Ctrl+Esc inside VS Code to open OpenCode.
exit /b 0

