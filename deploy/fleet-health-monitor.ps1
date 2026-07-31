<#
.SYNOPSIS
  Central runner-fleet health monitor. Runs on DeskComputer (interactive
  session, launched hidden via run-hidden.vbs), manages DeskComputer and
  ControlTower over Tailscale. One cycle per invocation; cadence comes from
  the scheduled-task repetition trigger (every 5 min).

  What it does each cycle:
    1. Ensure DeskComputer's own WSL-Runner-KeepAlive task is Running.
    2. ControlTower WMI-handle guard: kill any WmiPrvSE leaking > threshold
       handles (root cause of the 2026-06-11 ControlTower outage).
    3. Query the dashboard /api/runners (GitHub App auth, authoritative):
       a. per-pool online floors for Desktop / ControlTower-SSD / Oglaptop —
          the 2026-07-30 outage went unseen for weeks because only the
          CT-SSD count was watched, and GitHub auto-purged the silent pools'
          registrations after ~14 days offline;
       b. Desktop self-heal: below-floor Desktop pool -> start the local
          systemd runner units (in-place; never a WSL reset);
       c. purge alarm: zero Desktop runners online while local units run is
          the registration-purge signature -> ERROR pointing at
          docs/runbooks/runner-registration-purge-recovery.md;
       d. restart the ControlTower SSD keepalive task when its pool is low.
    4. Log + write state JSON.

  ControlTower remote ops go over SSH (key auth, host alias 'controltower').
  Never invokes wsl.exe over SSH (it hangs in the network-logon session and
  exhausts the desktop heap) -- all CT WSL work stays inside CT's own
  keepalive tasks.
#>
param(
  [string]$DashboardUrl          = "http://127.0.0.1:8321",
  [string]$CtSsh                 = "controltower",
  [int]   $WmiHandleKillThreshold = 120000,
  [int]   $CtRunnerMinOnline      = 6,
  [int]   $CtRunnerTotal          = 17,
  # Floors are REAL-OUTAGE thresholds, deliberately below nominal capacity:
  # GitHub's "online" flag flaps for idle runners (broker session cycling,
  # see 2026-05-29 postmortem), so a tight floor would false-alarm on any
  # idle fleet. What the floors must catch is silent pool decay toward the
  # ~14-day registration purge, where counts sit at/near zero for days.
  [hashtable]$PoolFloors          = @{ 'Desktop' = 3; 'ControlTower-SSD' = 6; 'Oglaptop' = 2 },
  [string]$DeskWslDistro          = "Ubuntu-22.04",
  [int]   $DeskRunnerTotal        = 8,
  [string]$LocalKeepAliveTask     = "WSL-Runner-KeepAlive",
  [string]$LogDir                 = "C:\Users\diete\runner_fleet_monitor",
  # Dot-source with -FunctionsOnly to expose the pure helpers for tests.
  [switch]$FunctionsOnly
)

# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/deploy/test_fleet_health_monitor.py)
# ---------------------------------------------------------------------------

$script:PoolPrefixes = @{
  'Desktop'          = 'd-sorg-local-Desktop-'
  'ControlTower-SSD' = 'd-sorg-local-ControlTower-SSD-'
  'Oglaptop'         = 'd-sorg-local-Oglaptop-'
}

function Get-RunnerPoolCounts {
  <# Classify runners into pools by name prefix.
     Postcondition: every configured pool key is present with online/total. #>
  param(
    [array]$Runners = @(),
    [hashtable]$PoolPrefixes = $script:PoolPrefixes
  )
  $counts = @{}
  foreach ($pool in $PoolPrefixes.Keys) {
    $members = @($Runners | Where-Object { $_.name -like ($PoolPrefixes[$pool] + '*') })
    $counts[$pool] = @{
      online = @($members | Where-Object { $_.status -eq 'online' }).Count
      total  = $members.Count
    }
  }
  return $counts
}

function Get-PoolsBelowFloor {
  <# Names of pools whose online count is below the configured floor. #>
  param(
    [Parameter(Mandatory)][hashtable]$Counts,
    [Parameter(Mandatory)][hashtable]$Floors
  )
  $below = @()
  foreach ($pool in $Floors.Keys) {
    if ($Counts.ContainsKey($pool) -and [int]$Counts[$pool].online -lt [int]$Floors[$pool]) {
      $below += $pool
    }
  }
  return $below
}

function Test-PurgeSuspected {
  <# GitHub deletes registrations for runners offline ~14 days. Zero pool
     members online while local units are actively running is that
     signature: listeners are up but the server no longer knows them. #>
  param(
    [Parameter(Mandatory)][int]$PoolOnline,
    [Parameter(Mandatory)][int]$LocalUnitsActive
  )
  return ($PoolOnline -eq 0 -and $LocalUnitsActive -ge 1)
}

if ($FunctionsOnly) { return }

# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------

$ErrorActionPreference = "Continue"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile   = Join-Path $LogDir "monitor.log"
$StateFile = Join-Path $LogDir "state.json"

function Write-Log([string]$msg, [string]$level = "INFO") {
  $ts = (Get-Date).ToString("o")
  Add-Content -Path $LogFile -Value "$ts [$level] $msg"
  if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 2MB)) {
    Move-Item $LogFile "$LogFile.1" -Force
  }
}

function Invoke-CtPowerShell([string]$Script, [int]$TimeoutSec = 30) {
  $bytes = [System.Text.Encoding]::Unicode.GetBytes($Script)
  $enc   = [Convert]::ToBase64String($bytes)
  $out = & ssh -o ConnectTimeout=10 -o BatchMode=yes $CtSsh "powershell -NoProfile -EncodedCommand $enc" 2>&1
  # Drop ssh/CLIXML stderr noise so keepalive-restart results stay readable
  # in monitor.log (remote powershell emits progress records as CLIXML).
  $clean = $out | ForEach-Object { [string]$_ } | Where-Object {
    $_ -notmatch '^#< CLIXML' -and
    $_ -notmatch '<Objs .*</Objs>' -and
    $_ -notmatch 'Warning: Permanently added' -and
    $_ -notmatch 'NativeCommandError|CategoryInfo|FullyQualifiedErrorId|^\s*\+\s'
  }
  return ($clean | Out-String)
}

function Get-DeskActiveUnitCount {
  <# Count active local Desktop runner units inside the WSL distro. #>
  $out = & wsl -d $DeskWslDistro -u dieterolson -e bash -c 'systemctl list-units --state=active --no-legend "actions.runner.*Desktop*" | wc -l' 2>$null
  $n = 0
  [void][int]::TryParse(([string]$out).Trim(), [ref]$n)
  return $n
}

function Start-DeskRunnerUnits {
  <# In-place unit starts only; never resets or restarts WSL itself. #>
  for ($n = 1; $n -le $DeskRunnerTotal; $n++) {
    & wsl -d $DeskWslDistro -u root -e systemctl start "actions.runner.D-sorganization.d-sorg-local-Desktop-$n.service" 2>$null
  }
}

$state = [ordered]@{
  timestamp          = (Get-Date).ToString("o")
  actions            = @()
  ct_wmi_max_handles = $null
  pool_counts        = $null
  pools_below_floor  = @()
  purge_suspected    = $false
  desk_keepalive     = $null
  errors             = @()
}

# -- 1. DeskComputer local keepalive -----------------------------------------
try {
  $t = Get-ScheduledTask -TaskName $LocalKeepAliveTask -ErrorAction Stop
  $state.desk_keepalive = "$($t.State)"
  if ($t.State -ne "Running") {
    Start-ScheduledTask -TaskName $LocalKeepAliveTask
    Write-Log "DeskComputer keepalive '$LocalKeepAliveTask' was $($t.State); started it." "WARN"
    $state.actions += "started-desk-keepalive"
  }
} catch {
  Write-Log "Failed to check/start DeskComputer keepalive: $($_.Exception.Message)" "ERROR"
  $state.errors += "desk-keepalive: $($_.Exception.Message)"
}

# -- 2. ControlTower WMI-handle guard ----------------------------------------
try {
  $guard = @"
`$killed = @()
`$max = 0
foreach (`$p in Get-Process WmiPrvSE -ErrorAction SilentlyContinue) {
  if (`$p.HandleCount -gt `$max) { `$max = `$p.HandleCount }
  if (`$p.HandleCount -gt $WmiHandleKillThreshold) {
    try { Stop-Process -Id `$p.Id -Force -ErrorAction Stop; `$killed += "`$(`$p.Id):`$(`$p.HandleCount)" } catch {}
  }
}
"WMIMAX=`$max"
"WMIKILLED=`$(`$killed -join ',')"
"@
  $res = Invoke-CtPowerShell $guard
  $maxLine = ($res -split "`n" | Where-Object { $_ -match "WMIMAX=" }) -replace ".*WMIMAX=", ""
  $killLine = ($res -split "`n" | Where-Object { $_ -match "WMIKILLED=" }) -replace ".*WMIKILLED=", ""
  $state.ct_wmi_max_handles = ($maxLine | Select-Object -First 1).Trim()
  if ($killLine -and ($killLine.Trim())) {
    Write-Log "ControlTower WMI guard KILLED leaking WmiPrvSE: $($killLine.Trim()) (threshold $WmiHandleKillThreshold)" "WARN"
    $state.actions += "killed-ct-wmiprvse:$($killLine.Trim())"
  }
} catch {
  Write-Log "ControlTower WMI guard failed: $($_.Exception.Message)" "ERROR"
  $state.errors += "ct-wmi: $($_.Exception.Message)"
}

# -- 3. Fleet pool floors + self-heal + CT keepalive restart ------------------
try {
  $runners = Invoke-RestMethod -Uri "$DashboardUrl/api/runners?local=true" -TimeoutSec 25
  $poolCounts = Get-RunnerPoolCounts -Runners $runners.runners
  $state.pool_counts = $poolCounts
  $summary = ($poolCounts.Keys | Sort-Object | ForEach-Object { "$($_)=$($poolCounts[$_].online)/$($poolCounts[$_].total)" }) -join ' '
  Write-Log "fleet pools online: $summary (WmiPrvSE max handles: $($state.ct_wmi_max_handles))"

  $below = Get-PoolsBelowFloor -Counts $poolCounts -Floors $PoolFloors
  $state.pools_below_floor = $below
  foreach ($pool in $below) {
    Write-Log "pool '$pool' online $($poolCounts[$pool].online) below floor $($PoolFloors[$pool])" "WARN"
  }

  # 3b. Desktop self-heal + registration-purge alarm (local machine only).
  if ($below -contains 'Desktop') {
    $localActive = Get-DeskActiveUnitCount
    if (Test-PurgeSuspected -PoolOnline ([int]$poolCounts['Desktop'].online) -LocalUnitsActive $localActive) {
      $state.purge_suspected = $true
      Write-Log ("Desktop pool: 0 online on GitHub while $localActive local units run - REGISTRATION PURGE " +
        "suspected. Re-register: see docs/runbooks/runner-registration-purge-recovery.md") "ERROR"
    } else {
      Start-DeskRunnerUnits
      Write-Log "Desktop pool below floor; started local runner units in-place." "WARN"
      $state.actions += "started-desk-runner-units"
    }
  }

  # 3c. ControlTower SSD keepalive restart when its pool is low.
  $ctOnline = [int]$poolCounts['ControlTower-SSD'].online
  if ($ctOnline -lt $CtRunnerMinOnline) {
    Write-Log "ControlTower SSD online ($ctOnline/$CtRunnerTotal) below min ($CtRunnerMinOnline); restarting SSD keepalive task." "WARN"
    $restart = @'
foreach ($tn in @("ControlTower-SSD-KeepAlive")) {
  $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
  if ($t -and $t.State -ne "Running") { Start-ScheduledTask -TaskName $tn; "restarted $tn" }
  elseif ($t) { "$tn already $($t.State)" }
  else { "missing $tn" }
}
'@
    $r = Invoke-CtPowerShell $restart
    Write-Log "Keepalive restart result: $($r.Trim() -replace '\s+',' ')"
    $state.actions += "restarted-ct-ssd-keepalive"
  }
} catch {
  Write-Log "Runner status query failed: $($_.Exception.Message)" "ERROR"
  $state.errors += "runners-api: $($_.Exception.Message)"
}

# -- 4. Persist state ---------------------------------------------------------
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile
