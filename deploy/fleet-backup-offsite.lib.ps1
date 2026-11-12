<#
.SYNOPSIS
    Helpers for rotated encrypted USB SSD offsite restic copies (#4009).
#>

[CmdletBinding()]
param(
    [switch]$FunctionsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertFrom-FleetBackupOffsiteEnvFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "config not found: $Path"
    }
    $settings = @{}
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $idx = $line.IndexOf('=')
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $settings[$key] = $value
    }
    return $settings
}

function Read-FleetBackupOffsiteStateFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return @{}
    }
    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @{}
    }
    $obj = $raw | ConvertFrom-Json
    $hash = @{}
    $obj.PSObject.Properties | ForEach-Object { $hash[$_.Name] = $_.Value }
    return $hash
}

function Write-FleetBackupOffsiteStateFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][hashtable]$State
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    ($State | ConvertTo-Json -Compress) | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-FleetBackupOffsiteRotationSlot {
    param(
        [hashtable]$State = @{},
        [Parameter(Mandatory)][datetime]$NowUtc
    )
    $week = [System.Globalization.ISOWeek]::GetWeekOfYear($NowUtc)
    $year = $NowUtc.Year
    $lastWeek = $null
    if ($State.ContainsKey('rotation_week') -and $State['rotation_week']) {
        $lastWeek = [int]$State['rotation_week']
    }
    $slot = if ($week % 2 -eq 0) { 'B' } else { 'A' }
    return @{
        slot       = $slot
        week       = $week
        year       = $year
        changed    = ($lastWeek -ne $week)
    }
}

function Resolve-FleetBackupOffsiteUsbRoot {
    param(
        [Parameter(Mandatory)][hashtable]$Config,
        [Parameter(Mandatory)][string]$Slot
    )
    $key = if ($Slot -eq 'B') { 'OFFSITE_USB_LABEL_B' } else { 'OFFSITE_USB_LABEL_A' }
    if (-not $Config.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($Config[$key])) {
        throw "missing config key $key"
    }
    $label = $Config[$key].Trim()
    $volume = Get-Volume -ErrorAction Stop | Where-Object {
        $_.FileSystemLabel -eq $label -and $_.DriveLetter
    } | Select-Object -First 1
    if (-not $volume) {
        throw "no mounted USB volume with label $label"
    }
    $root = "{0}:\" -f $volume.DriveLetter
    return @{
        Label      = $label
        DriveLetter = $volume.DriveLetter
        Root       = $root
    }
}

function Get-FleetBackupOffsiteResticCopyArguments {
    param(
        [Parameter(Mandatory)][string]$SourceRepository,
        [Parameter(Mandatory)][string]$UsbRepositoryRoot,
        [string[]]$Extra = @()
    )
    $root = $UsbRepositoryRoot.TrimEnd('\', '/')
    $dest = "$root/restic-offsite"
    $args = @(
        'copy',
        '--repo', $SourceRepository,
        '--repo2', $dest,
        '--json'
    )
    if ($Extra) {
        $args += $Extra
    }
    return ,@{
        Arguments = $args
        DestRepo  = $dest
    }
}
