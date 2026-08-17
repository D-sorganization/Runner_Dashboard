<#
.SYNOPSIS
    Install or update the scheduled task that runs the Windows MATLAB runner.

.DESCRIPTION
    Registers the \D-sorganization\matlab-runner scheduled task on Windows runner
    hosts (such as ControlTower). Configures a repeating 10-minute trigger on
    startup/logon with an execution limit to ensure automatic recovery if the
    runner listener exits on broker socket disconnections (exit code 3), matching
    the production hot-fix under issue #1078.

.EXAMPLE
    .\install-matlab-runner-task.ps1 -RunnerRoot 'C:\actions-runner' -DryRun
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskPath = '\D-sorganization\',
    [string]$TaskName = 'matlab-runner',
    [string]$RunnerRoot = 'C:\actions-runner',
    [int]$RepeatIntervalMinutes = 10,
    [string]$RunAsUser = '',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
    $RunAsUser = if ($env:USERDOMAIN -and $env:USERNAME) {
        "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
    } elseif ($env:USERNAME) {
        $env:USERNAME
    } else {
        'runner'
    }
}

if (-not $TaskPath.EndsWith('\')) {
    $TaskPath += '\'
}
if (-not $TaskPath.StartsWith('\')) {
    $TaskPath = '\' + $TaskPath
}

$startScript = Join-Path $RunnerRoot 'run.cmd'
$startScriptPs1 = Join-Path $RunnerRoot 'start-runner.ps1'
$executable = if (Test-Path $startScriptPs1) {
    (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
} elseif (Test-Path $startScript) {
    'cmd.exe'
} else {
    'cmd.exe'
}

$arguments = if (Test-Path $startScriptPs1) {
    "-NoProfile -ExecutionPolicy Bypass -File `"$startScriptPs1`""
} else {
    "/c `"$startScript`""
}

if ($DryRun) {
    [pscustomobject]@{
        task_path = $TaskPath
        task_name = $TaskName
        full_path = "$TaskPath$TaskName"
        executable = $executable
        arguments = $arguments
        repeat_minutes = $RepeatIntervalMinutes
        runas_user = $RunAsUser
        logon_type = 'S4U'
        run_level = 'Highest'
    } | ConvertTo-Json -Depth 4
    exit 0
}

$action = New-ScheduledTaskAction -Execute $executable -Argument $arguments -WorkingDirectory $RunnerRoot
$triggerStartup = New-ScheduledTaskTrigger -AtStartup
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

# 10-minute repeating trigger to ensure prompt recovery on listener exit
$triggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $RepeatIntervalMinutes)
$triggers = @($triggerStartup, $triggerLogon, $triggerRepeat)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Highest

if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", 'Register Windows MATLAB runner scheduled task')) {
    Register-ScheduledTask `
        -TaskPath $TaskPath `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Settings $settings `
        -Principal $principal `
        -Description "Runs and keeps Windows MATLAB runner listener resident with 10-min repetition (#1078)." `
        -Force | Out-Null

    Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Output "Installed scheduled task '$TaskPath$TaskName'."
}
