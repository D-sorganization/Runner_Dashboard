<#
.SYNOPSIS
    Export an OGLaptop WSL distro and back up the tar with restic (#4011).

    Scripts/docs/tests only while OGLaptop WSL is broken — use -DryRun for validation.
    Requires the fleet backup pilot repository config (same RESTIC_* keys as #4015).
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ConfigFile = '',
    [string]$ExportDir = '',
    [string]$StateFile = '',
    [string]$ResticPath = 'restic',
    [string]$DistroName = '',
    [switch]$DryRun,
    [switch]$SkipExport,
    [switch]$SkipResticBackup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'fleet-backup-wsl-export.lib.ps1') -FunctionsOnly

function Invoke-FleetBackupWslExport {
    param(
        [Parameter(Mandatory)][string]$DistroName,
        [Parameter(Mandatory)][string]$TarPath,
        [switch]$DryRun
    )
    $command = Format-FleetBackupWslExportCommand -DistroName $DistroName -TarPath $TarPath
    if ($DryRun) {
        Write-Output "DRY-RUN $command"
        return
    }
    if ($PSCmdlet.ShouldProcess($TarPath, "wsl export $DistroName")) {
        & wsl.exe --export $DistroName $TarPath
        if ($LASTEXITCODE -ne 0) {
            throw "wsl --export failed with exit code $LASTEXITCODE"
        }
    }
}

function Invoke-FleetBackupWslRestic {
    param(
        [Parameter(Mandatory)][string]$ResticPath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$DryRun
    )
    if ($DryRun) {
        Write-Output ('DRY-RUN restic ' + ($Arguments -join ' '))
        return 0
    }
    & $ResticPath @Arguments
    return $LASTEXITCODE
}

$paths = Resolve-FleetBackupWslExportPaths -ConfigFile $ConfigFile -ExportDir $ExportDir -StateFile $StateFile
$config = ConvertFrom-FleetBackupEnvFile -Path $paths.ConfigFile
$settings = Get-FleetBackupWslExportSettings -Config $config
if ($DistroName) { $settings = [pscustomobject]@{ DistroName = $DistroName; ResticRepository = $settings.ResticRepository; PasswordFile = $settings.PasswordFile } }

$env:RESTIC_REPOSITORY = $settings.ResticRepository
if ($settings.PasswordFile) { $env:RESTIC_PASSWORD_FILE = $settings.PasswordFile }

if (-not (Test-Path -LiteralPath $paths.ExportDir)) {
    if ($DryRun) {
        Write-Output "DRY-RUN mkdir $($paths.ExportDir)"
    }
    else {
        New-Item -ItemType Directory -Path $paths.ExportDir -Force | Out-Null
    }
}

$tarName = New-FleetBackupWslExportTarName -DistroName $settings.DistroName
$tarPath = Join-Path $paths.ExportDir $tarName

try {
    if (-not $SkipExport) {
        Invoke-FleetBackupWslExport -DistroName $settings.DistroName -TarPath $tarPath -DryRun:$DryRun
    }
    if (-not $DryRun -and (Test-Path -LiteralPath $tarPath)) {
        if (-not (Test-WslExportTarFooterComplete -TarPath $tarPath)) {
            throw 'export tar failed footer check (truncated export?)'
        }
    }
    if (-not $SkipResticBackup) {
        if ($DryRun -and -not (Test-Path -LiteralPath $tarPath)) {
            Write-Output "DRY-RUN restic backup (tar would be created first): $tarPath"
        }
        else {
            $resticArgs = Get-FleetBackupWslExportResticArguments -TarPath $tarPath
            if ((Invoke-FleetBackupWslRestic -ResticPath $ResticPath -Arguments $resticArgs -DryRun:$DryRun) -ne 0) {
                throw 'restic backup of WSL export failed'
            }
        }
    }
    if (-not $DryRun) {
        $state = @{
            distro = $settings.DistroName
            last_success_at = (Get-Date).ToUniversalTime().ToString('o')
            last_tar = $tarPath
            last_result = 'success'
        }
        ($state | ConvertTo-Json -Compress) | Set-Content -LiteralPath $paths.StateFile -Encoding UTF8
    }
    exit 0
}
catch {
    if (-not $DryRun) {
        $state = @{
            distro = $settings.DistroName
            last_attempt_at = (Get-Date).ToUniversalTime().ToString('o')
            last_result = 'failure'
            message = $_.Exception.Message
        }
        ($state | ConvertTo-Json -Compress) | Set-Content -LiteralPath $paths.StateFile -Encoding UTF8
    }
    Write-Error $_.Exception.Message
    exit 1
}
