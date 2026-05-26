<#
.SYNOPSIS
    WSL keepalive watchdog with responsiveness probe, structured logging,
    exponential backoff, and escalation policy.

.DESCRIPTION
    Replaces the trivial "is the distro listed as Running" check. That check
    misses the recurring failure where ``wsl --list --verbose`` shows the
    distro as Running but ``wsl -d <distro> -- echo`` times out with
    ``Wsl/Service/0x8007274c``. The runners and the dashboard look up
    completely with no process visible from the host.

    This script:
      * polls every ``CheckIntervalSeconds`` (default 30)
      * runs an actual command inside the distro with a hard timeout
        (default 8s); if it does not return, the distro is unresponsive
      * on unresponsive: ``wsl --shutdown`` then a clean start, with
        exponential backoff between consecutive recoveries so we do not
        thrash a sick host
      * writes one JSON line per state change to a rotating log file
      * persists health state to a small JSON file so external tooling
        (the dashboard, a Tailnet probe) can read it without running this
        script

.PARAMETER Distro
    The WSL distribution name to monitor. Default: ``Ubuntu-22.04``.

.PARAMETER CheckIntervalSeconds
    Seconds between health probes. Default 30. Minimum enforced: 5.

.PARAMETER ProbeTimeoutSeconds
    Seconds to wait for the in-distro echo before declaring it
    unresponsive. Default 8. Minimum enforced: 2.

.PARAMETER MaxConsecutiveRecoveries
    After this many recoveries in a row without a healthy gap of at least
    ``HealthyGapSeconds``, stop trying — the host needs human attention.
    Default 5.

.PARAMETER HealthyGapSeconds
    A successful probe this far after a recovery resets the consecutive
    recovery counter. Default 600 (10 min).

.PARAMETER LogDir
    Directory for ``wsl-keepalive.log`` (JSONL) and ``wsl-keepalive-state.json``.
    Default ``$env:LOCALAPPDATA\runner-dashboard``.

.PARAMETER MaxLogBytes
    Rotate the log when it exceeds this size. Default 5 MB.

.PARAMETER LogBackups
    Keep this many rotated backups. Default 3.

.PARAMETER DashboardPort
    Local runner-dashboard health port. Default 8321.

.PARAMETER DashboardServiceName
    systemd service to start when WSL is responsive but dashboard health fails.
    Default ``runner-dashboard.service``.

.PARAMETER Mode
    ``Watchdog`` preserves the legacy recovery behavior and may run
    ``wsl --shutdown`` when the distro or dashboard is wedged. ``Resident``
    keeps the selected distro warm with probes and dashboard-only recovery,
    but never resets WSL. Use ``Resident`` for split-disk runner fleets where
    one distro must not restart another pool.

.PARAMETER Once
    Run a single probe + recovery cycle and exit. Used by the test
    suite and by ad-hoc operator invocation.

.EXAMPLE
    # Production use (scheduled task entry point):
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\diete\wsl-keepalive.ps1

.EXAMPLE
    # One-shot health check:
    .\wsl-keepalive.ps1 -Once

.NOTES
    Owner: runner-dashboard
    See: runner-dashboard/docs/wsl-keepalive.md
    Issue origin: recurring DeskComputer dashboard outages, 2026-05-21..22
#>

[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu-22.04',
    [int]$CheckIntervalSeconds = 30,
    [int]$ProbeTimeoutSeconds = 8,
    [int]$MaxConsecutiveRecoveries = 5,
    [int]$HealthyGapSeconds = 600,
    [string]$LogDir = (Join-Path $env:LOCALAPPDATA 'runner-dashboard'),
    [int]$MaxLogBytes = 5MB,
    [int]$LogBackups = 3,
    [int]$DashboardPort = 8321,
    [string]$DashboardServiceName = 'runner-dashboard.service',
    [ValidateSet('Watchdog', 'Resident')]
    [string]$Mode = 'Watchdog',
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------- DbC: parameter validation -------------------------------------
if ([string]::IsNullOrWhiteSpace($Distro)) {
    throw "Distro must be a non-empty string"
}
if ($CheckIntervalSeconds -lt 5) {
    throw "CheckIntervalSeconds must be >= 5 (got $CheckIntervalSeconds)"
}
if ($ProbeTimeoutSeconds -lt 2) {
    throw "ProbeTimeoutSeconds must be >= 2 (got $ProbeTimeoutSeconds)"
}
if ($ProbeTimeoutSeconds -ge $CheckIntervalSeconds) {
    throw "ProbeTimeoutSeconds ($ProbeTimeoutSeconds) must be < CheckIntervalSeconds ($CheckIntervalSeconds)"
}
if ($MaxConsecutiveRecoveries -lt 1) {
    throw "MaxConsecutiveRecoveries must be >= 1 (got $MaxConsecutiveRecoveries)"
}
if ($HealthyGapSeconds -lt $CheckIntervalSeconds) {
    throw "HealthyGapSeconds ($HealthyGapSeconds) must be >= CheckIntervalSeconds ($CheckIntervalSeconds)"
}
if ($MaxLogBytes -lt 64KB) {
    throw "MaxLogBytes must be >= 64KB (got $MaxLogBytes)"
}
if ($LogBackups -lt 0) {
    throw "LogBackups must be >= 0 (got $LogBackups)"
}
if ($DashboardPort -lt 1 -or $DashboardPort -gt 65535) {
    throw "DashboardPort must be in 1..65535 (got $DashboardPort)"
}
if ([string]::IsNullOrWhiteSpace($DashboardServiceName)) {
    throw "DashboardServiceName must be a non-empty string"
}
if ($Mode -notin @('Watchdog', 'Resident')) {
    throw "Mode must be Watchdog or Resident (got $Mode)"
}

# ---------- File layout ----------------------------------------------------
$null = New-Item -ItemType Directory -Force -Path $LogDir
$LogPath = Join-Path $LogDir 'wsl-keepalive.log'
$StatePath = Join-Path $LogDir 'wsl-keepalive-state.json'

# ---------- Helpers (pure: no I/O, no globals) -----------------------------

function Get-BackoffSeconds {
    <#
    .SYNOPSIS
        Exponential backoff: 30s -> 60s -> 120s -> ... capped at 30min.

    .OUTPUTS
        [int] seconds to wait before the next recovery.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory)][int]$ConsecutiveRecoveries
    )
    if ($ConsecutiveRecoveries -lt 0) {
        throw "ConsecutiveRecoveries must be >= 0"
    }
    if ($ConsecutiveRecoveries -eq 0) { return 0 }
    $base = 30
    $cap = 1800  # 30 min
    $shift = [Math]::Min($ConsecutiveRecoveries - 1, 10)
    return [int][Math]::Min($cap, $base * [Math]::Pow(2, $shift))
}

function Test-Responsive {
    <#
    .SYNOPSIS
        Run a trivial command inside the distro with a hard timeout.

    .OUTPUTS
        [bool] $true iff the distro returned a non-empty stdout before timeout.

    .NOTES
        Uses Start-Process + WaitForExit($ms) rather than a job because
        background jobs survive PowerShell crashes and we want hard
        cleanup. The output is captured via a temp file because Start-Process
        cannot stream stdout from a hidden window directly.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][string]$Distro,
        [Parameter(Mandatory)][int]$TimeoutSeconds,
        [string]$WslExe = 'wsl.exe'
    )
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        $args = @('-d', $Distro, '--', '/bin/sh', '-c', 'echo alive')
        $p = Start-Process -FilePath $WslExe -ArgumentList $args `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile
        if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
            try { $p.Kill() } catch { }
            return $false
        }
        if ($p.ExitCode -ne 0) { return $false }
        $out = (Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue)
        return -not [string]::IsNullOrWhiteSpace($out)
    } finally {
        Remove-Item -LiteralPath $stdoutFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-WslRecovery {
    <#
    .SYNOPSIS
        Force-stop the WSL service then poke the distro back into life.

    .OUTPUTS
        [bool] $true if the distro is responsive after the recovery attempt.

    .NOTES
        ``wsl --shutdown`` kills every distro and the WSL2 lightweight VM.
        For DeskComputer this is acceptable because the dashboard host runs
        only one distro. Re-invoking ``wsl -d <distro> -- echo`` starts a
        cold VM and bootstraps systemd; the keepalive systemd unit then
        keeps it warm.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][string]$Distro,
        [Parameter(Mandatory)][int]$ProbeTimeoutSeconds,
        [string]$WslExe = 'wsl.exe'
    )
    & $WslExe --shutdown 2>$null | Out-Null
    Start-Sleep -Seconds 3
    # A first probe may time out while WSL is still booting; allow up to
    # 3x the normal probe budget for the post-shutdown cold start.
    return (Test-Responsive -Distro $Distro -TimeoutSeconds ($ProbeTimeoutSeconds * 3) -WslExe $WslExe)
}

function Test-DashboardHealth {
    <#
    .SYNOPSIS
        Check the Windows-local dashboard health endpoint.

    .OUTPUTS
        [bool] $true iff /health returns HTTP 200 before timeout.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutSeconds = 5
    )
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec $TimeoutSeconds -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-DashboardServiceOnly {
    <#
    .SYNOPSIS
        Start the dashboard service inside WSL without touching runner units.

    .OUTPUTS
        [bool] $true if the systemctl command exits successfully.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][string]$Distro,
        [Parameter(Mandatory)][string]$ServiceName,
        [Parameter(Mandatory)][int]$TimeoutSeconds,
        [string]$WslExe = 'wsl.exe'
    )
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        $script = "systemctl reset-failed $ServiceName 2>/dev/null || true; systemctl start $ServiceName 2>/dev/null || true"
        $args = @('-d', $Distro, '-u', 'root', '--exec', '/bin/bash', '-lc', $script)
        $p = Start-Process -FilePath $WslExe -ArgumentList $args `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile
        if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
            try { $p.Kill() } catch { }
            return $false
        }
        return $p.ExitCode -eq 0
    } finally {
        Remove-Item -LiteralPath $stdoutFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function Write-StateFile {
    <#
    .SYNOPSIS
        Persist a small JSON state document atomically (write to .tmp, rename).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][hashtable]$State
    )
    $tmp = "$Path.tmp"
    ($State | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Invoke-LogRotate {
    <#
    .SYNOPSIS
        Rotate ``$Path`` when it exceeds ``$MaxBytes``.

    .NOTES
        Style: ``foo.log -> foo.log.1 -> foo.log.2 -> ...``. The oldest backup
        beyond ``$Backups`` is discarded.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][int]$MaxBytes,
        [Parameter(Mandatory)][int]$Backups
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $size = (Get-Item -LiteralPath $Path).Length
    if ($size -le $MaxBytes) { return }
    for ($i = $Backups; $i -ge 1; $i--) {
        $src = if ($i -eq 1) { $Path } else { "$Path.$($i - 1)" }
        $dst = "$Path.$i"
        if (Test-Path -LiteralPath $src) {
            Move-Item -LiteralPath $src -Destination $dst -Force
        }
    }
}

function Write-EventLine {
    <#
    .SYNOPSIS
        Append a single JSONL event line; rotates first if needed.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][int]$MaxBytes,
        [Parameter(Mandatory)][int]$Backups,
        [Parameter(Mandatory)][hashtable]$Event
    )
    Invoke-LogRotate -Path $LogPath -MaxBytes $MaxBytes -Backups $Backups
    $Event['ts'] = (Get-Date).ToString('o')
    $line = ($Event | ConvertTo-Json -Compress -Depth 4)
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

# ---------- Main loop ------------------------------------------------------

function Invoke-OneCycle {
    <#
    .SYNOPSIS
        Run a single probe + (if needed) recovery + state-file update.
        Pure orchestration; the I/O helpers above carry the side effects.

    .OUTPUTS
        [hashtable] describing what happened, useful for tests and the
        scheduled-task summary.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string]$Distro,
        [Parameter(Mandatory)][int]$ProbeTimeoutSeconds,
        [Parameter(Mandatory)][int]$MaxConsecutive,
        [Parameter(Mandatory)][int]$HealthyGap,
        [Parameter(Mandatory)][string]$StatePath,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][int]$MaxLogBytes,
        [Parameter(Mandatory)][int]$LogBackups,
        [Parameter(Mandatory)][int]$DashboardPort,
        [Parameter(Mandatory)][string]$DashboardServiceName,
        [Parameter(Mandatory)][ValidateSet('Watchdog', 'Resident')][string]$Mode
    )

    # Load prior state (consecutive recovery counter, last_recovery_ts).
    $prior = @{ consecutive = 0; last_recovery_ts = $null; last_healthy_ts = $null }
    if (Test-Path -LiteralPath $StatePath) {
        try {
            $loaded = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
            $prior.consecutive = [int]$loaded.consecutive
            $prior.last_recovery_ts = $loaded.last_recovery_ts
            $prior.last_healthy_ts = $loaded.last_healthy_ts
        } catch {
            # corrupt state file -> start fresh, don't crash the watchdog
        }
    }

    $responsive = Test-Responsive -Distro $Distro -TimeoutSeconds $ProbeTimeoutSeconds
    $now = Get-Date

    if ($responsive) {
        $dashboardRecovered = $false
        if (-not (Test-DashboardHealth -Port $DashboardPort)) {
            Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
                level = 'warn'
                event = 'dashboard_unhealthy_detected'
                distro = $Distro
                port = $DashboardPort
            }
            $dashboardStartTimeout = [Math]::Max(60, $ProbeTimeoutSeconds * 3)
            $dashboardRecovered = Start-DashboardServiceOnly `
                -Distro $Distro `
                -ServiceName $DashboardServiceName `
                -TimeoutSeconds $dashboardStartTimeout
            if ($dashboardRecovered -and -not (Test-DashboardHealth -Port $DashboardPort)) {
                $dashboardRecovered = $false
            }
            Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
                level = if ($dashboardRecovered) { 'info' } else { 'error' }
                event = if ($dashboardRecovered) { 'dashboard_recovery_started' } else { 'dashboard_recovery_failed' }
                distro = $Distro
                service = $DashboardServiceName
                port = $DashboardPort
            }
            if (-not $dashboardRecovered) {
                if ($Mode -eq 'Resident') {
                    Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
                        level = 'error'
                        event = 'dashboard_recovery_failed_no_wsl_reset'
                        distro = $Distro
                        service = $DashboardServiceName
                        port = $DashboardPort
                        mode = $Mode
                    }
                } else {
                    Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
                        level = 'warn'
                        event = 'dashboard_recovery_escalating_to_wsl_reset'
                        distro = $Distro
                        service = $DashboardServiceName
                        port = $DashboardPort
                    }
                    $wslRecovered = Invoke-WslRecovery -Distro $Distro -ProbeTimeoutSeconds $ProbeTimeoutSeconds
                    if ($wslRecovered) {
                        $dashboardRecovered = Start-DashboardServiceOnly `
                            -Distro $Distro `
                            -ServiceName $DashboardServiceName `
                            -TimeoutSeconds $dashboardStartTimeout
                        if ($dashboardRecovered -and -not (Test-DashboardHealth -Port $DashboardPort)) {
                            $dashboardRecovered = $false
                        }
                    }
                    Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
                        level = if ($dashboardRecovered) { 'info' } else { 'error' }
                        event = if ($dashboardRecovered) { 'dashboard_recovery_after_wsl_reset_succeeded' } else { 'dashboard_recovery_after_wsl_reset_failed' }
                        distro = $Distro
                        service = $DashboardServiceName
                        port = $DashboardPort
                        wsl_recovered = $wslRecovered
                    }
                }
            }
        }

        # If we have been healthy for a full HealthyGap window since the
        # last recovery, reset the consecutive counter.
        $newConsecutive = $prior.consecutive
        if ($prior.last_recovery_ts) {
            $age = ($now - [DateTime]::Parse($prior.last_recovery_ts)).TotalSeconds
            if ($age -ge $HealthyGap -and $prior.consecutive -gt 0) {
                $newConsecutive = 0
            }
        }
        $state = @{
            status = 'healthy'
            consecutive = $newConsecutive
            last_recovery_ts = $prior.last_recovery_ts
            last_healthy_ts = $now.ToString('o')
            distro = $Distro
            dashboard_checked = $true
            dashboard_recovered = $dashboardRecovered
            mode = $Mode
        }
        Write-StateFile -Path $StatePath -State $state
        return @{ outcome = 'healthy'; consecutive = $newConsecutive; dashboard_recovered = $dashboardRecovered }
    }

    if ($Mode -eq 'Resident') {
        Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
            level = 'error'
            event = 'unresponsive_no_wsl_reset'
            consecutive = $prior.consecutive
            distro = $Distro
            mode = $Mode
        }
        $state = @{
            status = 'unresponsive'
            consecutive = $prior.consecutive
            last_recovery_ts = $prior.last_recovery_ts
            last_healthy_ts = $prior.last_healthy_ts
            distro = $Distro
            mode = $Mode
        }
        Write-StateFile -Path $StatePath -State $state
        return @{ outcome = 'unresponsive_no_reset'; consecutive = $prior.consecutive }
    }

    # Unresponsive: decide whether to recover or give up.
    if ($prior.consecutive -ge $MaxConsecutive) {
        Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
            level = 'error'
            event = 'recovery_budget_exhausted'
            consecutive = $prior.consecutive
            limit = $MaxConsecutive
            distro = $Distro
        }
        $state = @{
            status = 'failed'
            consecutive = $prior.consecutive
            last_recovery_ts = $prior.last_recovery_ts
            last_healthy_ts = $prior.last_healthy_ts
            distro = $Distro
            mode = $Mode
        }
        Write-StateFile -Path $StatePath -State $state
        return @{ outcome = 'budget_exhausted'; consecutive = $prior.consecutive }
    }

    Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
        level = 'warn'
        event = 'unresponsive_detected'
        consecutive_before = $prior.consecutive
        distro = $Distro
    }

    $recovered = Invoke-WslRecovery -Distro $Distro -ProbeTimeoutSeconds $ProbeTimeoutSeconds

    $newConsecutive = $prior.consecutive + 1
    Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
        level = if ($recovered) { 'info' } else { 'error' }
        event = if ($recovered) { 'recovery_succeeded' } else { 'recovery_failed' }
        consecutive = $newConsecutive
        distro = $Distro
    }

    $state = @{
        status = if ($recovered) { 'recovered' } else { 'failed' }
        consecutive = $newConsecutive
        last_recovery_ts = $now.ToString('o')
        last_healthy_ts = if ($recovered) { $now.ToString('o') } else { $prior.last_healthy_ts }
        distro = $Distro
        mode = $Mode
    }
    Write-StateFile -Path $StatePath -State $state
    return @{
        outcome = if ($recovered) { 'recovered' } else { 'recovery_failed' }
        consecutive = $newConsecutive
    }
}

# ---------- Entry point ----------------------------------------------------

if ($Once) {
    $result = Invoke-OneCycle `
        -Distro $Distro `
        -ProbeTimeoutSeconds $ProbeTimeoutSeconds `
        -MaxConsecutive $MaxConsecutiveRecoveries `
        -HealthyGap $HealthyGapSeconds `
        -StatePath $StatePath `
        -LogPath $LogPath `
        -MaxLogBytes $MaxLogBytes `
        -LogBackups $LogBackups `
        -DashboardPort $DashboardPort `
        -DashboardServiceName $DashboardServiceName `
        -Mode $Mode
    Write-Output ($result | ConvertTo-Json -Compress)
    exit 0
}

# Continuous mode: scheduled-task entry point.
Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
    level = 'info'
    event = 'watchdog_started'
    distro = $Distro
    interval_s = $CheckIntervalSeconds
    probe_timeout_s = $ProbeTimeoutSeconds
    max_consecutive = $MaxConsecutiveRecoveries
    mode = $Mode
}

while ($true) {
    try {
        $result = Invoke-OneCycle `
            -Distro $Distro `
            -ProbeTimeoutSeconds $ProbeTimeoutSeconds `
            -MaxConsecutive $MaxConsecutiveRecoveries `
            -HealthyGap $HealthyGapSeconds `
            -StatePath $StatePath `
            -LogPath $LogPath `
            -MaxLogBytes $MaxLogBytes `
            -LogBackups $LogBackups `
            -DashboardPort $DashboardPort `
            -DashboardServiceName $DashboardServiceName `
            -Mode $Mode
        # Backoff applies only after a recovery; healthy cycles use the
        # baseline interval. Keeps the script responsive when things are
        # fine and deliberate when WSL is sick.
        $extra = 0
        if ($result.outcome -in @('recovered','recovery_failed','budget_exhausted')) {
            $extra = Get-BackoffSeconds -ConsecutiveRecoveries $result.consecutive
        }
        Start-Sleep -Seconds ($CheckIntervalSeconds + $extra)
    } catch {
        # Never let the watchdog itself die; log and keep going.
        Write-EventLine -LogPath $LogPath -MaxBytes $MaxLogBytes -Backups $LogBackups -Event @{
            level = 'error'
            event = 'watchdog_exception'
            message = $_.Exception.Message
            stack = $_.ScriptStackTrace
        }
        Start-Sleep -Seconds $CheckIntervalSeconds
    }
}
