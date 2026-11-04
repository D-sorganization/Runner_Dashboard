<#
.SYNOPSIS
    Pure helpers for the OGLaptop fleet backup pilot (#4015).

    Dot-source with -FunctionsOnly for unit tests; the backup script imports
    these without running restic.
#>

[CmdletBinding()]
param(
    [switch]$FunctionsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertFrom-FleetBackupEnvFile {
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

function Test-FleetBackupPilotNeedsBandwidthProbe {
    param([hashtable]$State = @{})
    if ($State.ContainsKey('bandwidth_probe_completed_at') -and $State['bandwidth_probe_completed_at']) {
        return $false
    }
    return -not ($State.ContainsKey('bandwidth_mbps') -and $null -ne $State['bandwidth_mbps'])
}

function Measure-FleetBackupPilotBandwidthMbps {
    param(
        [Parameter(Mandatory)][string]$ProbeFile,
        [Parameter(Mandatory)][scriptblock]$Upload
    )
    if (-not (Test-Path -LiteralPath $ProbeFile)) {
        throw "probe file missing: $ProbeFile"
    }
    $bytes = (Get-Item -LiteralPath $ProbeFile).Length
    if ($bytes -lt 1) { throw 'probe file is empty' }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $Upload $ProbeFile
    $sw.Stop()
    if ($sw.Elapsed.TotalSeconds -le 0) {
        throw 'upload completed instantly; cannot compute bandwidth'
    }
    $mbps = ($bytes * 8.0) / ($sw.Elapsed.TotalSeconds * 1000000.0)
    return [math]::Round($mbps, 2)
}

function Get-FleetBackupResticArguments {
    param(
        [Parameter(Mandatory)][string[]]$BackupPaths,
        [switch]$UseFsSnapshot,
        [string[]]$Extra = @()
    )
    $args = @('backup', '--json')
    if ($UseFsSnapshot) {
        $args += '--use-fs-snapshot'
    }
    if ($Extra) {
        $args += $Extra
    }
    $args += $BackupPaths
    return ,$args
}

function Read-FleetBackupPilotStateFile {
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

function Write-FleetBackupPilotStateFile {
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

function Merge-FleetBackupPilotState {
    param(
        [hashtable]$Existing = @{},
        [hashtable]$Update = @{}
    )
    $merged = @{}
    foreach ($key in $Existing.Keys) { $merged[$key] = $Existing[$key] }
    foreach ($key in $Update.Keys) { $merged[$key] = $Update[$key] }
    return $merged
}

if (-not $FunctionsOnly) {
    throw 'Import fleet-backup-pilot.lib.ps1 with -FunctionsOnly or dot-source from oglaptop-fleet-backup.ps1'
}
