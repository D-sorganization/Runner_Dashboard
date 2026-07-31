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
  # Host free-space floors (GB) for the ControlTower drives. A WSL2 vhdx that
  # exhausts host disk mid-write corrupts the distro (null-byte files, corrupt
  # package DB) — the probable origin of the #1071 NVMe corruption, which on
  # 2026-07-31 came within minutes of repeating on the LIVE SSD pool. F: holds
  # the 326GB live runner vhdx, so its floor is the larger one.
  [hashtable]$DiskFloorsGb        = @{ 'C' = 25; 'F' = 40 },
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

function ConvertFrom-GitHubRunnerJson {
  <# Parse `gh api orgs/<org>/actions/runners` JSON into flat runner
     objects. Returns $null (never throws) on empty/invalid input so the
     caller can treat verification as unavailable. #>
  param([AllowEmptyString()][string]$Json = '')
  if ([string]::IsNullOrWhiteSpace($Json)) { return $null }
  try { $parsed = $Json | ConvertFrom-Json } catch { return $null }
  if (-not ($parsed.PSObject.Properties.Name -contains 'runners')) { return $null }
  return @($parsed.runners | ForEach-Object {
      [pscustomobject]@{ name = [string]$_.name; status = [string]$_.status }
    })
}

function Test-DiskBelowFloor {
  <# True when free space is known AND strictly below the floor. Unknown
     free space returns $false so an unparsable probe never fabricates a
     breach (the alarm must mean something when it fires). #>
  param(
    [AllowNull()]$FreeGb,
    [Parameter(Mandatory)][double]$FloorGb
  )
  if ($null -eq $FreeGb -or $FreeGb -eq '') { return $false }
  $value = 0.0
  if (-not [double]::TryParse([string]$FreeGb, [ref]$value)) { return $false }
  return ($value -lt $FloorGb)
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

function Invoke-CtPowerShell([string]$Script, [int]$TimeoutSec = 45) {
  <# Run a PowerShell snippet on ControlTower over SSH with a HARD deadline.
     Regression guard: on 2026-07-31 an un-deadlined ssh call hung a cycle
     for >100 minutes and MultipleInstances=IgnoreNew silently swallowed
     every later firing. A cycle must never hang. #>
  $bytes = [System.Text.Encoding]::Unicode.GetBytes($Script)
  $enc   = [Convert]::ToBase64String($bytes)
  $outFile = Join-Path $env:TEMP ("ct-ssh-" + [guid]::NewGuid().ToString('N') + ".out")
  try {
    $p = Start-Process -FilePath 'ssh' `
      -ArgumentList @('-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes', $CtSsh,
        "powershell -NoProfile -EncodedCommand $enc") `
      -NoNewWindow -PassThru -RedirectStandardOutput $outFile
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
      try { $p.Kill() } catch {}
      Write-Log "ct_ssh_timeout: ControlTower ssh exceeded ${TimeoutSec}s and was killed" "WARN"
      return ''
    }
    $out = Get-Content $outFile -ErrorAction SilentlyContinue
    # Drop CLIXML/progress noise so remote results stay readable in monitor.log.
    $clean = @($out) | ForEach-Object { [string]$_ } | Where-Object {
      $_ -notmatch '^#< CLIXML' -and
      $_ -notmatch '<Objs .*</Objs>' -and
      $_ -notmatch 'Warning: Permanently added'
    }
    return ($clean | Out-String)
  } finally {
    Remove-Item $outFile -Force -ErrorAction SilentlyContinue
  }
}

function Get-GitHubPoolCounts {
  <# Authoritative recount straight from the GitHub API. The dashboard feed
     can serve stale/false zeros under partial GitHub failure or auth
     throttling (observed 2026-07-31: 10 online reported vs 31 actual), so
     floor breaches are verified here before any warning or self-heal.
     Returns $null when gh is unavailable. #>
  $json = & gh api 'orgs/D-sorganization/actions/runners?per_page=100' 2>$null | Out-String
  $runners = ConvertFrom-GitHubRunnerJson -Json $json
  if ($null -eq $runners) { return $null }
  return Get-RunnerPoolCounts -Runners $runners
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
  ct_disk_free_gb    = $null
  pool_counts        = $null
  pools_below_floor  = @()
  purge_suspected    = $false
  desk_keepalive     = $null
  errors             = @()
}

# Heartbeat: a cycle that dies early must still leave a trace, otherwise a
# stall is indistinguishable from healthy silence.
Write-Log "cycle start"

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
foreach (`$dl in 'C', 'F') {
  `$v = Get-Volume -DriveLetter `$dl -ErrorAction SilentlyContinue
  # `${dl} braces are required: "`$dl:" would parse as a scope qualifier.
  if (`$v) { "DISKFREE=`${dl}:`$([math]::Round(`$v.SizeRemaining/1GB,2))" }
}
"@
  $res = Invoke-CtPowerShell $guard
  # Null-safe: a timed-out or failed ssh returns '', and calling .Trim() on the
  # resulting empty match set threw, aborting the whole section (incl. the disk
  # floors below) instead of degrading to "unknown".
  $maxLine = @($res -split "`n" | Where-Object { $_ -match "WMIMAX=" }) -replace ".*WMIMAX=", ""
  $killLine = @($res -split "`n" | Where-Object { $_ -match "WMIKILLED=" }) -replace ".*WMIKILLED=", ""
  $state.ct_wmi_max_handles = if ($maxLine.Count -gt 0) { ([string]$maxLine[0]).Trim() } else { $null }
  if ($killLine.Count -gt 0 -and ([string]$killLine[0]).Trim()) {
    $killed = ([string]$killLine[0]).Trim()
    Write-Log "ControlTower WMI guard KILLED leaking WmiPrvSE: $killed (threshold $WmiHandleKillThreshold)" "WARN"
    $state.actions += "killed-ct-wmiprvse:$killed"
  }

  # Host free-space floors. Deliberately alarm-only: reclaiming space means
  # deleting large artifacts, which is never safe to automate.
  $disk = @{}
  foreach ($line in ($res -split "`n" | Where-Object { $_ -match 'DISKFREE=' })) {
    if ($line -match 'DISKFREE=([A-Z]):([0-9.]+)') { $disk[$Matches[1]] = [double]$Matches[2] }
  }
  $state.ct_disk_free_gb = $disk
  foreach ($dl in $DiskFloorsGb.Keys) {
    if ($disk.ContainsKey($dl) -and (Test-DiskBelowFloor -FreeGb $disk[$dl] -FloorGb $DiskFloorsGb[$dl])) {
      Write-Log ("ControlTower ${dl}: only $($disk[$dl])GB free (floor $($DiskFloorsGb[$dl])GB) - " +
        "vhdx corruption risk: a WSL2 distro that exhausts host disk mid-write corrupts itself (see #1071). " +
        "Reclaim space now; do not wait.") "ERROR"
      $state.actions += "alarm-ct-disk-$dl"
    }
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
  if (@($below).Count -gt 0) {
    # The dashboard feed can be stale or falsely zeroed; verify any breach
    # against the GitHub API before warning or acting.
    $ghCounts = Get-GitHubPoolCounts
    if ($null -ne $ghCounts) {
      $ghBelow = @(Get-PoolsBelowFloor -Counts $ghCounts -Floors $PoolFloors)
      if (Compare-Object @($below) $ghBelow) {
        Write-Log ("dashboard runner feed disagrees with GitHub " +
          "(dashboard breach: $($below -join ',') vs verified: $($ghBelow -join ',')) - dashboard data suspect") "WARN"
      }
      $below = $ghBelow
      $poolCounts = $ghCounts
      $state.pool_counts = $ghCounts
    }
    else {
      Write-Log "floor breach reported by dashboard but GitHub verification unavailable; taking no action this cycle" "ERROR"
      $below = @()
    }
  }
  $state.pools_below_floor = $below
  foreach ($pool in $below) {
    Write-Log "pool '$pool' online $($poolCounts[$pool].online) below floor $($PoolFloors[$pool]) (GitHub-verified)" "WARN"
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
