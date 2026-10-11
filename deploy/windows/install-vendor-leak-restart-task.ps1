<#
.SYNOPSIS
    Register the scheduled task that restarts a leaking vendor service.

.DESCRIPTION
    Fleet memory policy (Repository_Management#2071). Dell ServiceShell
    (service DellClientManagementService) leaks about 2 GB/day and reached
    19.6 GB on ControlTower. This registers the SYSTEM task
    Fleet-Dell-ServiceShell-Restart, which restarts the service every 2 days
    at 04:30 and appends to C:\ProgramData\fleet-dell-restart.log.

    Idempotent (-Force re-registers in place). No-op, exit 0, on hosts where
    the service does not exist. Run elevated, once per host; it is not part of
    the WSL deploy path. Does not restart the service at install time.

.EXAMPLE
    pwsh -File deploy\windows\install-vendor-leak-restart-task.ps1
    pwsh -File deploy\windows\install-vendor-leak-restart-task.ps1 -Remove
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ServiceName = 'DellClientManagementService',
    [string]$TaskName = 'Fleet-Dell-ServiceShell-Restart',
    [string]$LogPath = 'C:\ProgramData\fleet-dell-restart.log',
    [int]$DaysInterval = 2,
    [string]$At = '4:30am',
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    }
    return
}

if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Write-Output "Service $ServiceName not present on this host; nothing to do."
    return
}

$command = "-NoProfile -ExecutionPolicy Bypass -Command `"Restart-Service $ServiceName -Force; Add-Content '$LogPath' (`"{0} restarted`" -f (Get-Date -Format o))`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $command
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $DaysInterval -At $At
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task (restart $ServiceName every $DaysInterval days)")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Restart $ServiceName (memory leak ~2 GB/day) every $DaysInterval days. RM#2071." `
        -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
        Select-Object TaskName, NextRunTime | Format-List | Out-String
}
