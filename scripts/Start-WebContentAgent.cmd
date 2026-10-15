@echo off
chcp 65001 >nul
title WebContentAgent Launcher
echo Starting LM Studio and WebContentAgent...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-ui-agent.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Keep this window open and send the error to Codex.
  pause
  exit /b 1
)
echo.
echo Ready. LM Studio should now be open with WebContentAgent available.
exit /b 0
