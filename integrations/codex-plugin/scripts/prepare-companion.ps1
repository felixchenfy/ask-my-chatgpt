$ErrorActionPreference = 'Stop'
$agentConfiguration = & node (Join-Path $PSScriptRoot 'resolve-agent.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the WebContentAgent environment.' }
$agentConfiguration = $agentConfiguration | ConvertFrom-Json
$bridgeAgentRoot = $agentConfiguration.cwd
$bridgePython = $agentConfiguration.executable
Push-Location $bridgeAgentRoot
try {
    & $bridgePython -m web_content_agent.chatgpt_pairing
    if ($LASTEXITCODE -ne 0) { throw 'Companion pairing preparation failed.' }
} finally { Pop-Location }
