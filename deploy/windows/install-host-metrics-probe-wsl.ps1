#Requires -Version 5.1
<#
.SYNOPSIS
    Install the WSL-side host-metrics-probe systemd timer from Windows (#4443).

.DESCRIPTION
    Fleet hubs (DeskComputer, ControlTower, OGLaptop) run runner-dashboard inside
    WSL. The probe must run there — not as a native Windows scheduled task.
    This helper invokes deploy/install-host-metrics-probe.sh inside the chosen
    distro. Elevation is only required on the WSL side when the dashboard uses
    a system-scoped unit (sudo for /etc/systemd/system).

.PARAMETER Distro
    WSL distro name (default: Ubuntu-22.04).

.PARAMETER DeployDir
    Deploy root inside WSL (default: ~/actions-runners/dashboard).

.EXAMPLE
    .\install-host-metrics-probe-wsl.ps1 -Distro Ubuntu-22.04
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Distro = 'Ubuntu-22.04',
    [string]$DeployDir = ''
)

$ErrorActionPreference = 'Stop'

$bashScript = @'
set -euo pipefail
deploy_dir="${1:-}"
if [[ -n "${deploy_dir}" ]]; then
  export RUNNER_DASHBOARD_DEPLOY_DIR="${deploy_dir}"
  installer="${deploy_dir}/deploy/install-host-metrics-probe.sh"
else
  installer="${HOME}/actions-runners/dashboard/deploy/install-host-metrics-probe.sh"
fi
if [[ -f "${installer}" ]]; then
  bash "${installer}"
else
  echo "Installer not found at ${installer} — deploy dashboard first" >&2
  exit 1
fi
'@

if ($PSCmdlet.ShouldProcess($Distro, 'Install host-metrics-probe timer inside WSL')) {
    # Native stdin from Windows PowerShell 5.1 adds CRLF. Write UTF-8 without
    # BOM and LF only, then let WSL read the file directly (no bash -c quoting).
    $scriptPath = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText(
            $scriptPath, $bashScript.Replace("`r", ""),
            (New-Object System.Text.UTF8Encoding($false))
        )
        $wslScriptPath = wsl.exe -d $Distro -e wslpath -u $scriptPath
        if ($LASTEXITCODE -ne 0) {
            throw "Could not translate temporary script path for WSL distro '$Distro'."
        }
        wsl.exe -d $Distro -e bash $wslScriptPath $DeployDir
        if ($LASTEXITCODE -ne 0) {
            throw "Host-metrics probe install failed in WSL distro '$Distro' (exit $LASTEXITCODE)."
        }
        Write-Output "Host-metrics probe install invoked in WSL distro '$Distro'."
        Write-Output "If the dashboard uses a system unit, the bash installer will prompt for sudo inside WSL."
    } finally {
        Remove-Item -LiteralPath $scriptPath -Force
    }
}
