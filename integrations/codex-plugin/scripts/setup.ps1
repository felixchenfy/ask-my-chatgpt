param(
    # Directories from which a local PDF may be attached. Pass your own list; the
    # default is only the current user's Documents folder.
    [string[]]$AllowedPdfRoots = @([Environment]::GetFolderPath('MyDocuments')),
    [int]$MaximumPdfSizeMb = 512
)

$stateRoot = Join-Path $env:USERPROFILE '.codex\chatgpt-pdf-review'
$configPath = Join-Path $stateRoot 'config.json'
$registryPath = Join-Path $stateRoot 'conversations.json'
# Resolve WebContentAgent like the MCP server: WCA_PROJECT_ROOT and
# WCA_PYTHON_EXECUTABLE win; otherwise the enclosing repository
# (integrations\codex-plugin -> ..\..) or a sibling WebContentAgent checkout.
$agentConfiguration = & node (Join-Path $PSScriptRoot 'resolve-agent.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the WebContentAgent environment. Set WCA_PROJECT_ROOT and, if needed, WCA_PYTHON_EXECUTABLE.' }
$agentConfiguration = $agentConfiguration | ConvertFrom-Json
$webContentAgent = $agentConfiguration.cwd
$pythonPath = $agentConfiguration.executable
$extensionPath = Join-Path $webContentAgent 'chrome_extension'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "WebContentAgent Python environment was not found at $pythonPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $extensionPath 'manifest.json') -PathType Leaf)) {
    throw "WebContentAgent Chrome Companion was not found at $extensionPath"
}

New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
& (Join-Path $PSScriptRoot 'prepare-companion.ps1')

$config = [ordered]@{
    allowedPdfRoots = @($AllowedPdfRoots | ForEach-Object { [IO.Path]::GetFullPath($_) })
    maximumPdfSizeMb = $MaximumPdfSizeMb
}
$config | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 -Path $configPath

if (-not (Test-Path -LiteralPath $registryPath)) {
    [ordered]@{ version = 1; conversations = [ordered]@{} } |
        ConvertTo-Json -Depth 4 |
        Set-Content -Encoding utf8 -Path $registryPath
}

Write-Host "Configuration written to $configPath"
Write-Host "Conversation registry ready at $registryPath"
Write-Host "WebContentAgent local MCP ready at $pythonPath"
Write-Host "Load or reload the unpacked Chrome Companion from $extensionPath only when its source changes."
