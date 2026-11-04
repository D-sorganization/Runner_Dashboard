<#
.SYNOPSIS
    OGLaptop pilot: restic to Desk rest-server with VSS (#4015). Use -InstallTask to register the daily job.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ConfigFile = '',
    [string]$StateFile = '',
    [string]$LogDir = '',
    [string]$ResticPath = 'restic',
    [string]$TaskName = 'RunnerDashboard-OGLaptop-FleetBackup-Pilot',
    [string]$RunAsUser = '',
    [string]$DailyTime = '02:30',
    [switch]$DryRun,
    [switch]$SkipBackup,
    [switch]$InstallTask,
    [switch]$UninstallTask
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'fleet-backup-pilot.lib.ps1') -FunctionsOnly

function Install-OGLaptopFleetBackupTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [string]$RunAsUser,
        [string]$DailyTime,
        [switch]$DryRun,
        [switch]$Uninstall
    )
    if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
        $RunAsUser = if ($env:USERDOMAIN -and $env:USERNAME) { "$($env:USERDOMAIN)\$($env:USERNAME)" } else { $env:USERNAME }
    }
    if ($Uninstall) {
        if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        return
    }
    $launcher = Join-Path $PSScriptRoot 'run-hidden.vbs'
    $psExe = (Get-Command powershell.exe).Source
    $inner = '-NoProfile -ExecutionPolicy Bypass -File "' + $ScriptPath + '"'
    $actionArgs = '//B //Nologo "' + $launcher + '" ' + $psExe + ' ' + $inner
    if ($DryRun) { Write-Output "DRY-RUN task $TaskName at $DailyTime for $RunAsUser"; return }
    $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') -Argument $actionArgs
    $trigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType InteractiveToken -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}

if ($InstallTask -or $UninstallTask) {
    Install-OGLaptopFleetBackupTask -TaskName $TaskName -ScriptPath $MyInvocation.MyCommand.Path -RunAsUser $RunAsUser -DailyTime $DailyTime -DryRun:$DryRun -Uninstall:$UninstallTask
    exit 0
}

function Resolve-FleetBackupPilotPaths {
    $programData = if ($env:ProgramData) { $env:ProgramData } else { 'C:\ProgramData' }
    $base = Join-Path $programData 'runner-dashboard'
    return [pscustomobject]@{
        ConfigFile = if ($ConfigFile) { $ConfigFile } else { Join-Path $base 'fleet-backup-pilot.env' }
        StateFile  = if ($StateFile) { $StateFile } else { Join-Path $base 'fleet_backup_pilot.json' }
        LogDir     = if ($LogDir) { $LogDir } else { Join-Path $base 'logs' }
    }
}

function Invoke-FleetBackupPilotRestic {
    param([string]$ResticPath, [string[]]$Arguments, [switch]$DryRun)
    if ($DryRun) { Write-Output ('DRY-RUN restic ' + ($Arguments -join ' ')); return 0 }
    & $ResticPath @Arguments
    return $LASTEXITCODE
}

$paths = Resolve-FleetBackupPilotPaths
$config = ConvertFrom-FleetBackupEnvFile -Path $paths.ConfigFile
if (-not $config['RESTIC_REPOSITORY']) { throw 'RESTIC_REPOSITORY missing' }
if ($config['RESTIC_PASSWORD_FILE']) { $env:RESTIC_PASSWORD_FILE = $config['RESTIC_PASSWORD_FILE'] }
$backupPaths = @($config['BACKUP_PATHS'].Split(';') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (-not $backupPaths) { throw 'BACKUP_PATHS missing' }

$state = Read-FleetBackupPilotStateFile -Path $paths.StateFile
$state = Merge-FleetBackupPilotState -Existing $state -Update @{
    pilot = 'oglaptop'; last_attempt_at = (Get-Date).ToUniversalTime().ToString('o'); last_result = 'running'
}
if (-not (Test-Path -LiteralPath $paths.LogDir)) { New-Item -ItemType Directory -Path $paths.LogDir -Force | Out-Null }

try {
    if (Test-FleetBackupPilotNeedsBandwidthProbe -State $state) {
        $probeDir = Join-Path $paths.LogDir 'bandwidth-probe'
        if (-not (Test-Path -LiteralPath $probeDir)) { New-Item -ItemType Directory -Path $probeDir -Force | Out-Null }
        $probeFile = Join-Path $probeDir 'probe.bin'
        if (-not (Test-Path -LiteralPath $probeFile)) {
            $bytes = New-Object byte[] (512 * 1024)
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            [System.IO.File]::WriteAllBytes($probeFile, $bytes)
        }
        $mbps = Measure-FleetBackupPilotBandwidthMbps -ProbeFile $probeFile -Upload {
            param($FilePath)
            $probeArgs = Get-FleetBackupResticArguments -BackupPaths @($FilePath) -UseFsSnapshot -Extra @('--tag', 'bandwidth-probe')
            if ((Invoke-FleetBackupPilotRestic -ResticPath $ResticPath -Arguments $probeArgs -DryRun:$DryRun) -ne 0) {
                throw 'bandwidth probe failed'
            }
        }
        $state = Merge-FleetBackupPilotState -Existing $state -Update @{
            bandwidth_mbps = $mbps; bandwidth_probe_completed_at = (Get-Date).ToUniversalTime().ToString('o')
        }
    }
    if (-not $SkipBackup) {
        $backupArgs = Get-FleetBackupResticArguments -BackupPaths $backupPaths -UseFsSnapshot -Extra @('--tag', 'oglaptop-pilot')
        if ((Invoke-FleetBackupPilotRestic -ResticPath $ResticPath -Arguments $backupArgs -DryRun:$DryRun) -ne 0) {
            throw 'restic backup failed'
        }
    }
    $state = Merge-FleetBackupPilotState -Existing $state -Update @{
        last_success_at = (Get-Date).ToUniversalTime().ToString('o'); last_result = 'success'; attention = $false; message = $null
    }
    if (-not $DryRun) { Write-FleetBackupPilotStateFile -Path $paths.StateFile -State $state }
    exit 0
}
catch {
    $state = Merge-FleetBackupPilotState -Existing $state -Update @{
        last_result = 'failure'; attention = $true; message = $_.Exception.Message
    }
    if (-not $DryRun) { Write-FleetBackupPilotStateFile -Path $paths.StateFile -State $state }
    Write-Error $_.Exception.Message
    exit 1
}
