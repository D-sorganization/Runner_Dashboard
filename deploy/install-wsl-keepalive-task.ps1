<#
.SYNOPSIS
    Install the Windows scheduled task that keeps a selected WSL distro resident.

.DESCRIPTION
    Registers a no-reset keepalive task for split-disk runner hosts. The task
    runs deploy/wsl-keepalive.ps1 in Resident mode, so it probes the requested
    distro often enough to prevent WSL idle shutdown and starts only the
    dashboard service when health fails. It deliberately does not reset WSL.

.EXAMPLE
    .\install-wsl-keepalive-task.ps1 -Distro WSL -TaskName ControlTower-NVMe-WSL-KeepAlive
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = 'RunnerDashboard-WSL-Resident-KeepAlive',
    [string]$Distro = 'WSL',
    [int]$CheckIntervalSeconds = 10,
    [int]$ProbeTimeoutSeconds = 3,
    [int]$DashboardPort = 8321,
    [string]$DashboardServiceName = 'runner-dashboard.service',
    [string]$ScriptPath = '',
    [string]$LogDir = (Join-Path $env:LOCALAPPDATA 'runner-dashboard'),
    [string]$RunAsUser = ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($TaskName)) {
    throw 'TaskName must be a non-empty string'
}
if ([string]::IsNullOrWhiteSpace($Distro)) {
    throw 'Distro must be a non-empty string'
}
if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
    $ScriptPath = Join-Path $PSScriptRoot 'wsl-keepalive.ps1'
}
if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    throw "Keepalive script not found: $ScriptPath"
}
if ($CheckIntervalSeconds -lt 5) {
    throw "CheckIntervalSeconds must be >= 5 (got $CheckIntervalSeconds)"
}
if ($ProbeTimeoutSeconds -lt 2 -or $ProbeTimeoutSeconds -ge $CheckIntervalSeconds) {
    throw "ProbeTimeoutSeconds must be >= 2 and < CheckIntervalSeconds (got $ProbeTimeoutSeconds)"
}
if ($DashboardPort -lt 1 -or $DashboardPort -gt 65535) {
    throw "DashboardPort must be in 1..65535 (got $DashboardPort)"
}

$pwsh = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $pwsh) {
    $pwsh = (Get-Command powershell.exe -ErrorAction Stop).Source
}

$arguments = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $ScriptPath,
    '-Distro', $Distro,
    '-CheckIntervalSeconds', [string]$CheckIntervalSeconds,
    '-ProbeTimeoutSeconds', [string]$ProbeTimeoutSeconds,
    '-DashboardPort', [string]$DashboardPort,
    '-DashboardServiceName', $DashboardServiceName,
    '-Mode', 'Resident',
    '-LogDir', $LogDir
)

if ($DryRun) {
    [pscustomobject]@{
        task_name = $TaskName
        executable = $pwsh
        arguments = $arguments -join ' '
        mode = 'Resident'
        distro = $Distro
        runas_user = $RunAsUser
        logon_type = 'S4U'
        run_level = 'Highest'
    } | ConvertTo-Json -Depth 4
    exit 0
}

$action = New-ScheduledTaskAction -Execute $pwsh -Argument ($arguments -join ' ')
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

# Run whether or not the user is logged on (S4U: no stored password). This is
# the keystone of split-disk fleet stability: the keepalive holds the host-side
# handle that keeps the WSL2 utility VM resident. Under an interactive-only
# logon trigger the task dies at logoff, the VM is torn down, and both distros
# cold-boot together on next logon — racing WSL's ~10s WaitForBootProcess
# timeout into the reboot(RB_POWER_OFF) crash loop. RunLevel Highest lets the
# resident probes manage systemd units inside the distro.
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Highest

if ($PSCmdlet.ShouldProcess($TaskName, 'Register WSL resident keepalive scheduled task')) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Settings $settings `
        -Principal $principal `
        -Description "Keeps WSL distro '$Distro' resident for runner-dashboard without WSL resets." `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Installed and started scheduled task '$TaskName' for distro '$Distro'."
}
