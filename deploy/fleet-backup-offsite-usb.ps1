<#
.SYNOPSIS
    Rotated encrypted USB SSD offsite restic copy (#4009). Requires BitLocker-ready USB volumes.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ConfigFile = '',
    [string]$StateFile = '',
    [string]$LogDir = '',
    [string]$ResticPath = 'restic',
    [switch]$DryRun,
    [switch]$SkipCopy
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'fleet-backup-offsite.lib.ps1') -FunctionsOnly

if (-not $ConfigFile) {
    $ConfigFile = Join-Path $PSScriptRoot 'fleet-backup-offsite.env'
}
if (-not $StateFile) {
    $StateFile = Join-Path $env:ProgramData 'RunnerDashboard\fleet-backup-offsite-state.json'
}
if (-not $LogDir) {
    $LogDir = Join-Path $env:ProgramData 'RunnerDashboard\logs\fleet-backup-offsite'
}

$config = ConvertFrom-FleetBackupOffsiteEnvFile -Path $ConfigFile
if (-not $config.ContainsKey('SOURCE_RESTIC_REPOSITORY')) {
    throw 'SOURCE_RESTIC_REPOSITORY is required in config'
}

$state = Read-FleetBackupOffsiteStateFile -Path $StateFile
$rotation = Get-FleetBackupOffsiteRotationSlot -State $state -NowUtc ([datetime]::UtcNow)
$usb = Resolve-FleetBackupOffsiteUsbRoot -Config $config -Slot $rotation.slot
$copy = Get-FleetBackupOffsiteResticCopyArguments `
    -SourceRepository $config['SOURCE_RESTIC_REPOSITORY'] `
    -UsbRepositoryRoot $usb.Root

Write-Output "offsite slot=$($rotation.slot) label=$($usb.Label) dest=$($copy.DestRepo)"

if ($DryRun) {
    Write-Output "DRY-RUN restic $($copy.Arguments -join ' ')"
    return
}

if ($SkipCopy) {
    Write-Output 'SkipCopy set; not running restic'
    return
}

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$log = Join-Path $LogDir ("offsite-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

if ($PSCmdlet.ShouldProcess($copy.DestRepo, 'restic copy to USB')) {
    & $ResticPath @($copy.Arguments) 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) {
        throw "restic copy failed with exit $LASTEXITCODE"
    }
}

$newState = @{}
foreach ($k in $state.Keys) { $newState[$k] = $state[$k] }
$newState['rotation_slot'] = $rotation.slot
$newState['rotation_week'] = $rotation.week
$newState['rotation_year'] = $rotation.year
$newState['last_success_at'] = ([datetime]::UtcNow).ToString('o')
$newState['last_usb_label'] = $usb.Label
Write-FleetBackupOffsiteStateFile -Path $StateFile -State $newState
