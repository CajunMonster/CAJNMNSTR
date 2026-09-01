param(
    [string]$TaskName = 'CAJNMNSTR Tuesday Competition Startup',
    [datetime]$At = [datetime]'2026-09-01T08:15:00',
    [switch]$TestMode
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$supervisorLauncher = Join-Path $PSScriptRoot 'Start-Competition-Supervisor.ps1'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $supervisorLauncher)) {
    throw 'The Competition Supervisor launcher is missing.'
}
if ($At -le [datetime]::Now -and -not $TestMode) {
    throw 'The requested competition startup time is not in the future.'
}

$arguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"' + $supervisorLauncher + '"')
)
if ($TestMode) {
    $arguments += @('-TestMode', '-CheckSeconds', '2', '-MaximumRestarts', '1')
}

$actionParameters = @{
    Execute = $powershell
    Argument = $arguments -join ' '
    WorkingDirectory = $projectRoot
}
$action = New-ScheduledTaskAction @actionParameters
$trigger = New-ScheduledTaskTrigger -Once -At $At
$principalParameters = @{
    UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    LogonType = 'Interactive'
    RunLevel = 'Limited'
}
$principal = New-ScheduledTaskPrincipal @principalParameters
$settingsParameters = @{
    MultipleInstances = 'IgnoreNew'
    StartWhenAvailable = $true
    RunOnlyIfNetworkAvailable = $true
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    WakeToRun = $true
    ExecutionTimeLimit = (New-TimeSpan -Hours 10)
}
$settings = New-ScheduledTaskSettingsSet @settingsParameters

$taskParameters = @{
    Action = $action
    Trigger = $trigger
    Principal = $principal
    Settings = $settings
    Description = 'Starts the local CAJNMNSTR supervised autonomous PAPER competition runtime.'
}
$task = New-ScheduledTask @taskParameters
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

$registered = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    task_name = $registered.TaskName
    state = $registered.State.ToString()
    next_run_time = $info.NextRunTime.ToString('o')
    owner_account = $registered.Principal.UserId
    logon_type = $registered.Principal.LogonType.ToString()
    multiple_instances = $registered.Settings.MultipleInstances.ToString()
    working_directory = $registered.Actions[0].WorkingDirectory
    supervisor_launcher = $supervisorLauncher
    test_mode = [bool]$TestMode
    secrets_in_arguments = $false
} | ConvertTo-Json
