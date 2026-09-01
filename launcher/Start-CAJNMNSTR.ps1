param(
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localUrl = 'http://127.0.0.1:8841/'
$healthUrl = 'http://127.0.0.1:8841/health.json'
$runtimeRoot = Join-Path $projectRoot 'runtime'
$serverStatePath = Join-Path $runtimeRoot 'server.json'
$nodeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin'
$vinextShim = Join-Path $projectRoot 'node_modules\.bin\vinext.CMD'

function Test-CAJNMNSTRHealth {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        $knownState = $health.state -in @('HEALTHY', 'DEGRADED', 'PAUSED')
        $safeMode = $health.mode -in @(
            'PAPER_READ_ONLY',
            'REPLAY_READ_ONLY',
            'PAPER_AUTONOMOUS_ARMED'
        )
        $brokerSubmissionBlocked = $health.broker_submission_allowed -eq $false
        return (
            $health.app -eq 'CAJNMNSTR' -and
            $knownState -and
            $safeMode -and
            $brokerSubmissionBlocked
        )
    }
    catch {
        return $false
    }
}

function Show-CAJNMNSTRError([string]$message) {
    if ($NoOpen) {
        Write-Error $message
        return
    }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($message, 'CAJNMNSTR', 'OK', 'Error') | Out-Null
}

if (Test-CAJNMNSTRHealth) {
    if (-not $NoOpen) {
        Start-Process -FilePath $localUrl
    }
    exit 0
}

$listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8841 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Show-CAJNMNSTRError 'Port 8841 is occupied by another application. CAJNMNSTR did not start, and the other process was not changed.'
    exit 2
}

if (-not (Test-Path -LiteralPath $vinextShim)) {
    Show-CAJNMNSTRError 'The local CAJNMNSTR runtime is incomplete. The page was not started.'
    exit 3
}

if (-not (Test-Path -LiteralPath (Join-Path $nodeRoot 'node.exe'))) {
    Show-CAJNMNSTRError 'The local Node.js runtime could not be found. Open Codex and rebuild the local page runtime.'
    exit 4
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$env:PATH = "$nodeRoot;$env:PATH"
$env:WRANGLER_LOG_PATH = Join-Path $runtimeRoot 'wrangler.log'

# The local operator dashboard consumes JSON that the live loop updates in public/.
# The development server serves those files from disk; the production server freezes
# them into the build output and can leave the operator looking at stale state.
$server = Start-Process -FilePath $vinextShim -ArgumentList @('dev', '--hostname', '127.0.0.1', '--port', '8841') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
[pscustomobject]@{
    app = 'CAJNMNSTR'
    pid = $server.Id
    started_at = [DateTimeOffset]::Now.ToString('o')
    url = $localUrl
} | ConvertTo-Json | Set-Content -LiteralPath $serverStatePath -Encoding utf8

$healthy = $false
for ($attempt = 0; $attempt -lt 80; $attempt++) {
    Start-Sleep -Milliseconds 250
    if (Test-CAJNMNSTRHealth) {
        $healthy = $true
        break
    }
    if ($server.HasExited) {
        break
    }
}

if (-not $healthy) {
    Show-CAJNMNSTRError 'CAJNMNSTR did not become healthy on port 8841. No browser was opened.'
    exit 5
}

$activeListener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8841 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($activeListener) {
    [pscustomobject]@{
        app = 'CAJNMNSTR'
        pid = $activeListener.OwningProcess
        started_at = [DateTimeOffset]::Now.ToString('o')
        url = $localUrl
    } | ConvertTo-Json | Set-Content -LiteralPath $serverStatePath -Encoding utf8
}

if (-not $NoOpen) {
    Start-Process -FilePath $localUrl
}
