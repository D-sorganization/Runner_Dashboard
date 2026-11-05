<#
.SYNOPSIS
    Pure helpers for OGLaptop WSL distro export into restic (#4011).

    Dot-source with -FunctionsOnly for tests. Does not invoke wsl.exe or restic
    unless the caller script does so explicitly.
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

function Resolve-FleetBackupWslExportPaths {
    param(
        [string]$ConfigFile = '',
        [string]$ExportDir = '',
        [string]$StateFile = ''
    )
    if ($IsWindows -or ($env:OS -eq 'Windows_NT')) {
        $programData = if ($env:ProgramData) { $env:ProgramData } else { 'C:\ProgramData' }
        $base = Join-Path $programData 'runner-dashboard'
    }
    else {
        if ($env:RUNNER_DASHBOARD_STATE_DIR) {
            $base = $env:RUNNER_DASHBOARD_STATE_DIR
        }
        elseif ($env:XDG_STATE_HOME) {
            $base = Join-Path $env:XDG_STATE_HOME 'runner-dashboard'
        }
        else {
            $base = '/var/lib/runner-dashboard'
        }
    }
    return [pscustomobject]@{
        ConfigFile = if ($ConfigFile) { $ConfigFile } else { Join-Path $base 'fleet-backup-wsl-export.env' }
        ExportDir  = if ($ExportDir) { $ExportDir } else { Join-Path $base 'wsl-exports' }
        StateFile  = if ($StateFile) { $StateFile } else { Join-Path $base 'fleet_backup_wsl_export.json' }
    }
}

function Get-FleetBackupWslExportSettings {
    param([Parameter(Mandatory)][hashtable]$Config)
    $distro = ($Config['WSL_DISTRO_NAME'] | ForEach-Object { $_.Trim() })
    if (-not $distro) { throw 'WSL_DISTRO_NAME missing' }
    $repo = ($Config['RESTIC_REPOSITORY'] | ForEach-Object { $_.Trim() })
    if (-not $repo) { throw 'RESTIC_REPOSITORY missing' }
    return [pscustomobject]@{
        DistroName = $distro
        ResticRepository = $repo
        PasswordFile = ($Config['RESTIC_PASSWORD_FILE'] | ForEach-Object { $_.Trim() })
    }
}

function New-FleetBackupWslExportTarName {
    param(
        [Parameter(Mandatory)][string]$DistroName,
        [datetime]$TimestampUtc = [datetime]::UtcNow
    )
    $safe = ($DistroName -replace '[^\w\-]+', '_')
    return "{0}-wsl-export-{1:yyyyMMdd-HHmmss}Z.tar" -f $safe, $TimestampUtc
}

function Test-WslExportTarFooterComplete {
    param(
        [Parameter(Mandatory)][string]$TarPath,
        [int]$FooterBytes = 1024
    )
    if (-not (Test-Path -LiteralPath $TarPath)) {
        return $false
    }
    $length = (Get-Item -LiteralPath $TarPath).Length
    if ($length -lt $FooterBytes) {
        return $false
    }
    $stream = [System.IO.File]::OpenRead($TarPath)
    try {
        $stream.Seek($length - $FooterBytes, [System.IO.SeekOrigin]::Begin) | Out-Null
        $buffer = New-Object byte[] $FooterBytes
        $read = $stream.Read($buffer, 0, $FooterBytes)
        if ($read -ne $FooterBytes) {
            return $false
        }
        foreach ($byte in $buffer) {
            if ($byte -ne 0) { return $false }
        }
        return $true
    }
    finally {
        $stream.Dispose()
    }
}

function Get-FleetBackupWslExportResticArguments {
    param(
        [Parameter(Mandatory)][string]$TarPath,
        [string[]]$Extra = @()
    )
    $args = @('backup', '--json', '--tag', 'wsl-export', '--tag', 'node-b')
    if ($Extra) { $args += $Extra }
    $args += $TarPath
    return ,$args
}

function Format-FleetBackupWslExportCommand {
    param(
        [Parameter(Mandatory)][string]$DistroName,
        [Parameter(Mandatory)][string]$TarPath
    )
    return "wsl.exe --export `"$DistroName`" `"$TarPath`""
}
