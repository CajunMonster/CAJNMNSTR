param(
    [ValidateRange(30, 300)]
    [int]$CheckSeconds = 30,
    [ValidateRange(1, 10)]
    [int]$MaximumRestarts = 3
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $projectRoot '.venv\Scripts\cajnmnstr.exe'
$dashboardLauncher = Join-Path $PSScriptRoot 'Start-CAJNMNSTR.ps1'
$dashboardPath = Join-Path $projectRoot 'public\dashboard-state.json'
$healthPath = Join-Path $projectRoot 'public\health.json'
$runtimeRoot = Join-Path $projectRoot 'runtime'
$stdoutPath = Join-Path $runtimeRoot 'competition-loop.stdout.log'
$stderrPath = Join-Path $runtimeRoot 'competition-loop.stderr.log'
$watchdogPath = Join-Path $runtimeRoot 'competition-supervisor.json'
$incidentPath = Join-Path $runtimeRoot 'incidents.jsonl'

function Write-SupervisorIncident([string]$code, [string]$detail) {
    [pscustomobject]@{
        occurred_at = [DateTimeOffset]::UtcNow.ToString('o')
        source = 'competition_supervisor_watchdog'
        severity = 'CRITICAL'
        code = $code
        detail = $detail
        protective_action = 'Keep new exposure paused; restart, recover durable state, and reconcile.'
        broker_submission_allowed = $false
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $incidentPath -Encoding utf8
}

function Read-RedactedConfiguration {
    $raw = & $cli config-check
    if ($LASTEXITCODE -ne 0) {
        throw 'Redacted configuration validation failed.'
    }
    return $raw | ConvertFrom-Json
}

function Test-DashboardHealth {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8841/health.json' -TimeoutSec 2
        return (
            $health.app -eq 'CAJNMNSTR' -and
            $health.broker_submission_allowed -eq $false
        )
    }
    catch {
        return $false
    }
}

function Start-LocalDashboard {
    if (-not (Test-DashboardHealth)) {
        & $dashboardLauncher -NoOpen
        if ($LASTEXITCODE -ne 0 -or -not (Test-DashboardHealth)) {
            Write-SupervisorIncident 'DASHBOARD_STALE' 'Independent dashboard restart did not verify healthy.'
        }
    }
}

function Start-CompetitionLoop([bool]$managePosition) {
    $arguments = @(
        'live-loop',
        '--cadence-seconds', '60',
        '--dashboard-path', $dashboardPath,
        '--health-path', $healthPath
    )
    if ($managePosition) {
        $arguments += @(
            '--manage-position',
            '--confirm', 'PAPER_POSITION_MANAGEMENT_LOOP'
        )
    }
    else {
        $arguments += @('--confirm', 'PAPER_READ_ONLY_LOOP')
    }
    return Start-Process -FilePath $cli -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
}

function Get-HeartbeatAgeSeconds {
    try {
        $state = Get-Content -LiteralPath $dashboardPath -Raw | ConvertFrom-Json
        if ($state.market.session -notin @('OPEN', 'REGULAR')) {
            return 0
        }
        $heartbeat = [DateTimeOffset]::Parse($state.supervisor.updated_at)
        return ([DateTimeOffset]::UtcNow - $heartbeat.ToUniversalTime()).TotalSeconds
    }
    catch {
        return 181
    }
}

if (-not (Test-Path -LiteralPath $cli)) {
    throw 'CAJNMNSTR local runtime is unavailable.'
}
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$configuration = Read-RedactedConfiguration
if ($configuration.entry_enabled -or $configuration.entry_armed) {
    throw 'Competition Supervisor refuses to start while new-entry authority is enabled or armed.'
}
if ($configuration.broker_lock_active) {
    throw 'Competition Supervisor refuses to start while the hard broker lock is active.'
}
$managePosition = [bool]$configuration.position_management_armed
Start-LocalDashboard

$restartCount = 0
$loop = Start-CompetitionLoop $managePosition
while ($true) {
    [pscustomobject]@{
        app = 'CAJNMNSTR'
        supervisor = 'competition_watchdog'
        loop_pid = $loop.Id
        loop_alive = -not $loop.HasExited
        restart_count = $restartCount
        checked_at = [DateTimeOffset]::UtcNow.ToString('o')
        entry_enabled = $false
        broker_submission_allowed = $false
    } | ConvertTo-Json | Set-Content -LiteralPath $watchdogPath -Encoding utf8

    Start-Sleep -Seconds $CheckSeconds
    $loop.Refresh()
    Start-LocalDashboard

    $restartReason = $null
    if ($loop.HasExited) {
        if ($loop.ExitCode -eq 0) {
            break
        }
        $restartReason = "Loop process exited with code $($loop.ExitCode)."
    }
    elseif ((Get-HeartbeatAgeSeconds) -gt 180) {
        $restartReason = 'Regular-session supervisor heartbeat exceeded three 60-second cadences.'
        Stop-Process -Id $loop.Id -Force
        $loop.WaitForExit()
    }

    if ($null -ne $restartReason) {
        Write-SupervisorIncident 'LOOP_STALLED' $restartReason
        $restartCount += 1
        if ($restartCount -gt $MaximumRestarts) {
            Write-SupervisorIncident 'RECOVERY_EXHAUSTED' 'Bounded loop restart limit was reached.'
            break
        }
        $configuration = Read-RedactedConfiguration
        if ($configuration.entry_enabled -or $configuration.entry_armed) {
            Write-SupervisorIncident 'UNSAFE_AUTHORITY_STATE' 'Entry authority changed during recovery.'
            break
        }
        $managePosition = [bool]$configuration.position_management_armed
        $loop = Start-CompetitionLoop $managePosition
    }
}
