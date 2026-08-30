param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$gitSafety = @('-c', "safe.directory=$($projectRoot.Replace('\', '/'))")
$blocked = [System.Collections.Generic.List[object]]::new()
$binaryExtensions = @('.gif', '.ico', '.jpeg', '.jpg', '.pdf', '.png', '.woff', '.woff2')
$prohibitedNamePattern = '(?i)(?<![A-Za-z0-9])' + 'MN' + 'STR(?:\s+Trade)?(?![A-Za-z0-9])'
$privateWorkspaceName = 'would-you-' + 'like-to-compete-in'
$patterns = [ordered]@{
    ProhibitedPrivateName = $prohibitedNamePattern
    PrivateAbsolutePath = '(?i)(?:[A-Z]:[\\/](?:Users|Documents)[\\/]|' + [regex]::Escape($privateWorkspaceName) + '|OneDrive[\\/]|Desktop[\\/])'
    NonEmptyCredentialAssignment = '(?im)^[ \t]*(?:ALPACA_API_KEY|ALPACA_SECRET_KEY|OPENAI_API_KEY)[ \t]*=[ \t]*(?!#|$).+$'
    OpenAITokenSignature = '(?i)(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])'
    AlpacaHeaderCredential = '(?i)(?:APCA-API-KEY-ID|APCA-API-SECRET-KEY)[ \t]*[:=][ \t]*["'']?(?!\$\{|<|your|fixture)[A-Za-z0-9_-]{12,}'
}

Push-Location $projectRoot
try {
    & git @gitSafety check-ignore -q -- .env.local
    $envLocalIgnored = $LASTEXITCODE -eq 0
    $rawFiles = & git @gitSafety ls-files --cached --others --exclude-standard -z
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to enumerate Git publication candidates.'
    }
    $files = @($rawFiles -split "`0" | Where-Object { $_ })

    foreach ($relativePath in $files) {
        foreach ($entry in $patterns.GetEnumerator()) {
            if ($relativePath -match $entry.Value) {
                $blocked.Add([pscustomobject]@{ Category = $entry.Key; Path = $relativePath })
            }
        }
        if ([IO.Path]::GetExtension($relativePath).ToLowerInvariant() -in $binaryExtensions) {
            continue
        }
        $fullPath = Join-Path $projectRoot $relativePath
        try {
            $content = [IO.File]::ReadAllText($fullPath).Replace("`r`n", "`n")
        }
        catch {
            $blocked.Add([pscustomobject]@{ Category = 'UnreadableCandidate'; Path = $relativePath })
            continue
        }
        foreach ($entry in $patterns.GetEnumerator()) {
            if (
                $entry.Key -eq 'NonEmptyCredentialAssignment' -and
                -not ([IO.Path]::GetFileName($relativePath).StartsWith('.env'))
            ) {
                continue
            }
            if ($content -match $entry.Value) {
                $blocked.Add([pscustomobject]@{ Category = $entry.Key; Path = $relativePath })
            }
        }
    }

    [pscustomobject]@{
        candidate_file_count = $files.Count
        env_local_ignored = $envLocalIgnored
        blocked_match_count = $blocked.Count
        status = if ($envLocalIgnored -and $blocked.Count -eq 0) { 'PASS' } else { 'FAIL' }
    } | ConvertTo-Json
    foreach ($finding in $blocked) {
        "BLOCKED $($finding.Category) $($finding.Path)"
    }
    if (-not $envLocalIgnored -or $blocked.Count -ne 0) {
        exit 1
    }
}
finally {
    Pop-Location
}
