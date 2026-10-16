@echo off
setlocal
rem CHATGPT_BRIDGE_SOURCE may point at another plugin checkout (for example
rem <WebContentAgent>\integrations\codex-plugin); otherwise run the copy containing this launcher.
if not defined CHATGPT_BRIDGE_SOURCE set "CHATGPT_BRIDGE_SOURCE=%~dp0.."
node "%CHATGPT_BRIDGE_SOURCE%\server.mjs"
