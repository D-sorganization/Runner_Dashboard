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
    [string]$LogDir = '',
    [string]$RunAsUser = '',
    [ValidateSet('Interactive', 'InteractiveToken', 'S4U', 'SYSTEM')][string]$LogonType = 'Interactive',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-WslPrincipalCompatible {
    [CmdletBinding()]
    param(
        [string]$User,
        [string]$LogonType = 'Interactive'
    )
    if ([string]::IsNullOrWhiteSpace($User)) {
        return [pscustomobject]@{
            Compatible = $false
            Reason = "User principal must be specified and non-empty."
            Principal = $User
            LogonType = $LogonType
        }
    }
    $incompatibleUsers = @('SYSTEM', 'NT AUTHORITY\SYSTEM', 'LocalSystem', 'S-1-5-18')
    if ($incompatibleUsers -contains $User.Trim() -or $User.Trim() -match '^(?i)((NT AUTHORITY\\)?SYSTEM|LocalSystem|S-1-5-18)$') {
        return [pscustomobject]@{
            Compatible = $false
            Reason = "WSL keepalive cannot run under SYSTEM (WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED). An interactive user principal is required."
            Principal = $User
            LogonType = $LogonType
        }
    }
    if ($LogonType -notin @('Interactive', 'InteractiveToken')) {
        return [pscustomobject]@{
            Compatible = $false
            Reason = "WSL keepalive requires LogonType Interactive or InteractiveToken (got '$LogonType'). S4U is unsupported for user WSL registrations."
            Principal = $User
            LogonType = $LogonType
        }
    }
    return [pscustomobject]@{
        Compatible = $true
        Reason = "Compatible interactive user principal."
        Principal = $User
        LogonType = $LogonType
    }
}

if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } elseif ($env:HOME) { [System.IO.Path]::Combine($env:HOME, '.runner-dashboard') } else { [System.IO.Path]::GetTempPath() }
    $LogDir = [System.IO.Path]::Combine($localAppData, 'runner-dashboard')
}
if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
    $RunAsUser = if ($env:USERDOMAIN -and $env:USERNAME) {
        "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
    } elseif ($env:USERNAME) {
        $env:USERNAME
    } else {
        ''
    }
}

$principalCheck = Test-WslPrincipalCompatible -User $RunAsUser -LogonType $LogonType
if (-not $principalCheck.Compatible) {
    throw $principalCheck.Reason
}

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

$pwshCmd = (Get-Command pwsh.exe -ErrorAction SilentlyContinue)
if (-not $pwshCmd) {
    $pwshCmd = (Get-Command pwsh -ErrorAction SilentlyContinue)
}
if (-not $pwshCmd) {
    $pwshCmd = (Get-Command powershell.exe -ErrorAction SilentlyContinue)
}
$pwsh = if ($pwshCmd) { $pwshCmd.Source } else { 'powershell.exe' }

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
        logon_type = $LogonType
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

# Run under a WSL-capable user principal (Interactive token: no stored password).
# S4U and SYSTEM principals cannot access user-scoped WSL registrations
# (SYSTEM fails with WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED and S4U lacks user session state).
# RunLevel Highest lets the resident probes manage systemd units inside the distro.
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType $LogonType -RunLevel Highest

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
