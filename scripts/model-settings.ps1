$WcaModelFile = 'Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-Q6_K.gguf'
$WcaRuntimeKey = 'qwen3.5-9b-the-defiant-fable-uncensored-heretic-neo-imatrix-max-mtp'
$WcaConcreteKey = 'DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF/' + $WcaModelFile
$WcaDownloadUrl = 'https://huggingface.co/DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF/blob/main/' + $WcaModelFile

function Set-WcaBionicModelPreferences {
    $RuntimeSettingsPath = Join-Path $env:USERPROFILE '.lmstudio\apps\bionic\settings.json'
    if (Test-Path -LiteralPath $RuntimeSettingsPath) {
        $RuntimeSettings = Get-Content -LiteralPath $RuntimeSettingsPath -Raw | ConvertFrom-Json
        if ($RuntimeSettings.useLlamaCppEngineProtocolRuntime3 -eq $true) {
            $RuntimeSettings.useLlamaCppEngineProtocolRuntime3 = $false
            $RuntimeTempPath = "$RuntimeSettingsPath.wca.tmp"
            $RuntimeJson = $RuntimeSettings | ConvertTo-Json -Depth 100
            [IO.File]::WriteAllText(
                $RuntimeTempPath,
                $RuntimeJson,
                (New-Object Text.UTF8Encoding($false))
            )
            Move-Item -LiteralPath $RuntimeTempPath -Destination $RuntimeSettingsPath -Force
            Write-Host 'Disabled Bionic Runtime 3 auto-fit so context-length is enforceable.'
        }
    }

    $SettingsPath = Join-Path $env:USERPROFILE '.lmstudio\apps\bionic\.internal\settings.json'
    if (-not (Test-Path -LiteralPath $SettingsPath)) { return }

    $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    $Changed = $false
    if (-not $Settings.sessions) {
        $Settings | Add-Member -NotePropertyName sessions -NotePropertyValue ([pscustomobject]@{})
    }
    $Current = $Settings.sessions.lastSelectedModelSpecifier
    if (-not $Current -or $Current.type -ne 'indexed' -or
        $Current.indexedModelIdentifier -ne $WcaConcreteKey) {
        $Specifier = [pscustomobject]@{
            type = 'indexed'
            indexedModelIdentifier = $WcaConcreteKey
        }
        if ($Settings.sessions.PSObject.Properties.Name -contains 'lastSelectedModelSpecifier') {
            $Settings.sessions.lastSelectedModelSpecifier = $Specifier
        } else {
            $Settings.sessions | Add-Member -NotePropertyName lastSelectedModelSpecifier -NotePropertyValue $Specifier
        }
        $Changed = $true
        Write-Host 'Set Bionic new-chat default to the pinned DavidAU uncensored VLM.'
    }

    if ($Changed) {
        $TempPath = "$SettingsPath.wca.tmp"
        $Json = $Settings | ConvertTo-Json -Depth 100
        [IO.File]::WriteAllText($TempPath, $Json, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $TempPath -Destination $SettingsPath -Force
    }
}
