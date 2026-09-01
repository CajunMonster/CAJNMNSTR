param(
    [ValidateRange(1, 300)]
    [int]$CheckSeconds = 30,
    [ValidateRange(1, 10)]
    [int]$MaximumRestarts = 3,
    [switch]$TestMode
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
$startupLogPath = Join-Path $runtimeRoot 'competition-startup.jsonl'
$mutexName = 'Local\CAJNMNSTRCompetitionSupervisor'

if ($TestMode) {
    $dashboardPath = Join-Path $runtimeRoot 'startup-test-dashboard.json'
    $healthPath = Join-Path $runtimeRoot 'startup-test-health.json'
}

function Write-StartupEvent(
    [string]$code,
    [string]$severity,
    [string]$detail
) {
    [pscustomobject]@{
        occurred_at = [DateTimeOffset]::UtcNow.ToString('o')
        source = 'competition_scheduled_startup'
        severity = $severity
        code = $code
        detail = $detail
        test_mode = [bool]$TestMode
        credentials_recorded = $false
        broker_submission_allowed = $false
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $startupLogPath -Encoding utf8
}

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

function Start-CompetitionLoop([bool]$managePosition, [bool]$autonomous) {
    $arguments = @(
        'live-loop',
        '--cadence-seconds', '60',
        '--dashboard-path', $dashboardPath,
        '--health-path', $healthPath
    )
    if ($TestMode) {
        $arguments += @(
            '--no-order-test',
            '--confirm', 'PAPER_NO_ORDER_STARTUP_TEST',
            '--max-cycles', '1'
        )
    }
    elseif ($autonomous) {
        $arguments += @(
            '--manage-position',
            '--autonomous',
            '--confirm', 'PAPER_AUTONOMOUS_COMPETITION'
        )
    }
    elseif ($managePosition) {
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

function Get-CleanTerminalState {
    try {
        $state = Get-Content -LiteralPath $dashboardPath -Raw | ConvertFrom-Json
        $regularSessionComplete = (
            $state.supervisor.loop_state -eq 'REGULAR_SESSION_COMPLETE' -and
            $state.supervisor.loop_advancing -eq $false -and
            $state.market.session -eq 'MARKET CLOSED' -and
            $state.supervisor.broker_reconciled -eq $true -and
            [int]$state.supervisor.position_count -eq 0 -and
            [int]$state.supervisor.open_order_count -eq 0 -and
            $state.supervisor.broker_submission_allowed -eq $false
        )
        if ($regularSessionComplete) {
            return 'REGULAR_SESSION_COMPLETE'
        }
    }
    catch {
        return $null
    }
    return $null
}

function Write-WatchdogState(
    [System.Diagnostics.Process]$process,
    [bool]$alive,
    [int]$restarts,
    [string]$terminalState
) {
    [pscustomobject]@{
        app = 'CAJNMNSTR'
        supervisor = 'competition_watchdog'
        loop_pid = $process.Id
        loop_alive = $alive
        restart_count = $restarts
        checked_at = [DateTimeOffset]::UtcNow.ToString('o')
        terminal_state = $terminalState
        entry_enabled = [bool]$configuration.entry_enabled
        autonomous_entry_mode = $autonomous
        broker_submission_allowed = $false
    } | ConvertTo-Json | Set-Content -LiteralPath $watchdogPath -Encoding utf8
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

$createdNew = $false
$supervisorMutex = [System.Threading.Mutex]::new(
    $true,
    $mutexName,
    [ref]$createdNew
)
if (-not $createdNew) {
    Write-StartupEvent 'DUPLICATE_SUPERVISOR_BLOCKED' 'INFO' 'An existing local Competition Supervisor owns the runtime mutex.'
    $supervisorMutex.Dispose()
    exit 0
}

try {
    Write-StartupEvent 'STARTUP_REQUESTED' 'INFO' 'Local Competition Supervisor startup began.'
    if (-not (Test-Path -LiteralPath $cli)) {
        throw 'CAJNMNSTR local runtime is unavailable.'
    }
    $configuration = Read-RedactedConfiguration
    if ($configuration.broker_lock_active) {
        throw 'Competition Supervisor refuses to start while the hard broker lock is active.'
    }
    $autonomous = [bool]$configuration.entry_armed
    if ($configuration.entry_enabled -and -not $autonomous) {
        throw 'Enabled entry authority is not fully armed; refusing ambiguous autonomous state.'
    }
    if ($autonomous -and -not $configuration.position_management_armed) {
        throw 'Autonomous entry requires armed deterministic position management.'
    }
    if ($autonomous -and $configuration.session_loss_limit_usd -ne '2000') {
        throw 'Autonomous competition mode requires the owner-approved $2,000 session limit.'
    }
    $managePosition = [bool]$configuration.position_management_armed
    Start-LocalDashboard

    $restartCount = 0
    $cleanTerminalState = $null
    $loop = Start-CompetitionLoop $managePosition $autonomous
    Write-StartupEvent 'SUPERVISOR_PROCESS_STARTED' 'INFO' 'The supervised PAPER loop process started after redacted safety validation.'
    while ($true) {
        Write-WatchdogState $loop (-not $loop.HasExited) $restartCount $null

        Start-Sleep -Seconds $CheckSeconds
        $loop.Refresh()
        Start-LocalDashboard

        $restartReason = $null
        if ($loop.HasExited) {
            $loop.WaitForExit()
            $cleanTerminalState = Get-CleanTerminalState
            if ($TestMode -and $loop.ExitCode -eq 0) {
                $cleanTerminalState = 'STARTUP_TEST_COMPLETE'
            }
            if ($cleanTerminalState -eq 'REGULAR_SESSION_COMPLETE' -or $cleanTerminalState -eq 'STARTUP_TEST_COMPLETE') {
                Write-WatchdogState $loop $false $restartCount $cleanTerminalState
                break
            }
            $restartReason = "Loop process exited with code $($loop.ExitCode)."
        }
        elseif (-not $TestMode -and (Get-HeartbeatAgeSeconds) -gt 180) {
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
            $autonomous = [bool]$configuration.entry_armed
            if ($configuration.broker_lock_active) {
                Write-SupervisorIncident 'UNSAFE_AUTHORITY_STATE' 'Broker lock activated during recovery.'
                break
            }
            if ($configuration.entry_enabled -and -not $autonomous) {
                Write-SupervisorIncident 'UNSAFE_AUTHORITY_STATE' 'Entry authority became ambiguous during recovery.'
                break
            }
            if ($autonomous -and (-not $configuration.position_management_armed -or $configuration.session_loss_limit_usd -ne '2000')) {
                Write-SupervisorIncident 'UNSAFE_AUTHORITY_STATE' 'Autonomous entry lost position-management or session-risk authority.'
                break
            }
            $managePosition = [bool]$configuration.position_management_armed
            $loop = Start-CompetitionLoop $managePosition $autonomous
        }
    }
    if ($null -ne $cleanTerminalState) {
        Write-StartupEvent 'SUPERVISOR_STOPPED' 'INFO' ("The supervised loop reached clean terminal state: " + $cleanTerminalState + '.')
    }
    else {
        Write-StartupEvent 'SUPERVISOR_STOPPED_FAIL_CLOSED' 'CRITICAL' 'The watchdog stopped after bounded recovery without a clean terminal state.'
    }
}
catch {
    Write-StartupEvent 'STARTUP_FAILED' 'CRITICAL' ("Startup failed closed: " + $_.Exception.Message)
    Write-SupervisorIncident 'STARTUP_FAILED' 'The scheduled Competition Supervisor did not establish a safe runtime.'
    throw
}
finally {
    $supervisorMutex.ReleaseMutex()
    $supervisorMutex.Dispose()
}
