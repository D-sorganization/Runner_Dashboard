<#
.SYNOPSIS
d-overnight-supervisor.ps1 - Blocker-keyed, 1-card-per-lane CLI supervisor (Panel th_2f46d79d092d, issue #4574).
#>
param(
  [int]$MaxHours = 10, [int]$PollSec = 90,
  [string]$StopFile = "C:\Users\diete\.rd-sessions\STOP_SUPERVISOR",
  [string]$LogFile = "C:\Users\diete\.rd-sessions\overnight-supervisor.log",
  [string]$DormantFile = "C:\Users\diete\.rd-sessions\blocker-cache.json",
  [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$RdSession = 'C:\Users\diete\.agents\rd-session\rd-session.ps1'
$Inventory = 'C:\Users\diete\.agents\rd-session\rd-fleet-inventory.ps1'
$InvJson = 'C:\Users\diete\OpenBot\Shared\fleet-inventory\inventory.json'
$FleetGh = 'C:\Users\diete\.claude\fleet-gh\bin'
if (Test-Path $FleetGh) { $env:PATH = "$FleetGh;$env:PATH" }

function Get-DormancyKey($task, $head, $blocker, $dep_version) {
  $t = if ($task) { "$task" } else { "none" }
  $h = if ($head) { "$head" } else { "none" }
  $b = if ($blocker) { "$blocker" } else { "none" }
  $d = if ($dep_version) { "$dep_version" } else { "v1" }
  return "$t@$h#$b@$d"
}

$SkipItems = @(
  'Repository_Management#3012', 'Repository_Management#2890', 'Repository_Management#3029', 'Repository_Management#3024',
  'Runner_Dashboard#2852', 'Runner_Dashboard#4547', 'Runner_Dashboard#4562', 'Runner_Dashboard#4564',
  'Gasification_Model#5873', 'AffineDrift#5579', 'AffineDrift#5781'
)

$lanes = @(
  @{ lane='claude-ct-1';       host='controltower'; prov='claude'; model='sonnet';        role='acceptance-review';  ud=$true },
  @{ lane='codex-ct-test';     host='controltower'; prov='codex';  model='gpt-6.1-sol';   role='acceptance-review';  ud=$true },
  @{ lane='codex-desk-1';      host='deskcomputer'; prov='codex';  model='gpt-6.1-terra'; role='backlog-completion'; ud=$false },
  @{ lane='codex-desk-test';   host='deskcomputer'; prov='codex';  model='gpt-6.1-terra'; role='backlog-completion'; ud=$false },
  @{ lane='claude-desk-1';     host='deskcomputer'; prov='claude'; model='sonnet';        role='backlog-completion'; ud=$false },
  @{ lane='agy-desk-1';        host='deskcomputer'; prov='agy';    model='gemini-3.8-flash'; role='backlog-completion'; ud=$false },
  @{ lane='codex-oglaptop-1';  host='oglaptop';     prov='codex';  model='gpt-6.1-terra'; role='backlog-completion'; ud=$false },
  @{ lane='codex-oglaptop-2';  host='oglaptop';     prov='codex';  model='gpt-6.1-terra'; role='backlog-completion'; ud=$false },
  @{ lane='claude-oglaptop-1'; host='oglaptop';     prov='claude'; model='sonnet';        role='backlog-completion'; ud=$false }
)

$dormant = @{}
if (Test-Path $DormantFile) {
  try {
    $rawD = Get-Content $DormantFile -Raw | ConvertFrom-Json
    foreach ($p in $rawD.PSObject.Properties) { $dormant[$p.Name] = $p.Value }
  } catch {}
}

$lastLaunch = @{}; $laneCard = @{}; $crashWait = @{}
$deadline = (Get-Date).AddHours($MaxHours)
"[$(Get-Date -Format o)] SUPERVISOR_STARTED max_hours=$MaxHours poll_sec=$PollSec panel=th_2f46d79d092d" | Add-Content $LogFile

function Get-ExpiredProviders {
  $expired = @{}
  try {
    $s = Invoke-RestMethod -Uri 'http://127.0.0.1:8321/api/staff/summary' -TimeoutSec 30
    $auth = $s.provider_auth.machines
    if ($auth) {
      foreach ($m in $auth.PSObject.Properties) {
        $hn = $m.Name.ToLower() -replace '-runner$',''
        foreach ($pv in $m.Value.providers.PSObject.Properties) {
          if ($pv.Value.status -eq 'expired') { $expired["$hn`:$($pv.Name)"] = $true }
        }
      }
    }
  } catch {}
  return $expired
}

function Get-ReadyCards {
  $cards = @()
  $repos = @('Runner_Dashboard', 'Repository_Management', 'AffineDrift', 'Gasification_Model', 'Tools', 'UpstreamDrift')
  foreach ($r in $repos) {
    try {
      $prs = gh pr list -R "D-sorganization/$r" --state open --limit 20 --json 'number,isDraft,mergeStateStatus,labels,headRefOid,title' | ConvertFrom-Json
      foreach ($p in $prs) {
        if ($p.isDraft) { continue }
        $key = "$r#$($p.number)"
        if ($SkipItems -contains $key) { continue }
        $lbls = @($p.labels | ForEach-Object { $_.name })
        if ($lbls -contains 'do-not-merge' -or $lbls -contains 'needs-owner') { continue }
        $kind = if ($p.mergeStateStatus -eq 'CLEAN') { 'review' } elseif ($p.mergeStateStatus -in @('DIRTY','BLOCKED','UNSTABLE')) { 'repair' } else { continue }
        $cards += [pscustomobject]@{ key=$key; repo=$r; num=$p.number; head=$p.headRefOid.Substring(0,7); kind=$kind; is_ud=($r -eq 'UpstreamDrift'); title=$p.title; blocker='none'; dep_version='v1' }
      }
    } catch {}
  }
  return $cards
}

$cachedCards = @(); $lastCardFetch = (Get-Date).AddMinutes(-10)

while ((Get-Date) -lt $deadline) {
  if (Test-Path $StopFile) { "[$(Get-Date -Format o)] STOP_FILE_DETECTED exiting" | Add-Content $LogFile; break }
  if ($DryRun) { "[$(Get-Date -Format o)] DRY_RUN_COMPLETE" | Add-Content $LogFile; break }
  try {
    if (Test-Path $Inventory) { & $Inventory | Out-Null }
    $inv = if (Test-Path $InvJson) { Get-Content $InvJson -Raw | ConvertFrom-Json } else { $null }
    $hubLanes = if ($inv.hub_lanes) { $inv.hub_lanes } elseif ($inv.lanes) { $inv.lanes } else { @() }
    $laneMap = @{}
    foreach ($hl in $hubLanes) {
      $k = if ($hl.lane) { $hl.lane } else { $hl.lane_id }
      if ($k) { $laneMap[$k] = $hl }
    }
    if (((Get-Date) - $lastCardFetch).TotalSeconds -ge 180) { $cachedCards = Get-ReadyCards; $lastCardFetch = Get-Date }
    $expired = Get-ExpiredProviders
    $assignedKeys = @($laneCard.Values | Where-Object { $_ } | ForEach-Object { if ($_.key) { $_.key } else { ($_ -split '@')[0] } })

    foreach ($l in $lanes) {
      $cur = $laneMap[$l.lane]
      if (-not $cur -or "$($cur.state)" -notmatch 'IDLE') { continue }
      if ($expired["$($l.host):$($l.prov)"]) { continue }

      if ($lastLaunch[$l.lane]) {
        $ranSec = ((Get-Date) - $lastLaunch[$l.lane]).TotalSeconds
        if ($laneCard[$l.lane] -and $ranSec -lt 360) {
          $prev = $laneCard[$l.lane]
          $pKey = if ($prev.key) { "$($prev.key)" } else { "$prev" -split '@' | Select-Object -First 1 }
          $pHead = if ($prev.head) { "$($prev.head)" } else { "head" }
          $pBlocker = if ($prev.blocker) { "$($prev.blocker)" } else { "fast_exit" }
          $pDep = if ($prev.dep_version) { "$($prev.dep_version)" } else { "v1" }
          $dKey = Get-DormancyKey $pKey $pHead $pBlocker $pDep
          $dormant[$dKey] = [pscustomobject]@{ until = (Get-Date).AddMinutes(60).ToString('o'); task = $pKey; head = $pHead; blocker = $pBlocker; dep_version = $pDep; reason = 'fast_exit' }
          $dormant | ConvertTo-Json -Depth 4 | Set-Content $DormantFile -Encoding UTF8
          $laneCard[$l.lane] = $null
        } elseif ($ranSec -lt 360) { $crashWait[$l.lane] = (Get-Date).AddMinutes(15) }
      }
      if ($crashWait[$l.lane] -and (Get-Date) -lt $crashWait[$l.lane]) { continue }

      $pool = if ($l.role -eq 'acceptance-review') { $cachedCards | Where-Object { $_.kind -eq 'review' } } elseif ($l.ud) { $cachedCards | Where-Object { $_.is_ud } } else { $cachedCards | Where-Object { -not $_.is_ud -and $_.kind -eq 'repair' } }
      $picked = $null
      foreach ($c in $pool) {
        if ($assignedKeys -contains $c.key) { continue }
        $dk = Get-DormancyKey $c.key $c.head $c.blocker $c.dep_version
        if ($dormant[$dk] -and ([datetime]$dormant[$dk].until) -gt (Get-Date)) { continue }
        $picked = $c; break
      }

      $focus = if ($picked) {
        $laneCard[$l.lane] = $picked; $assignedKeys += $picked.key
        "ASSIGNED TASK CARD (Panel th_2f46d79d092d, claim on active RM board): Work ONLY on $($picked.key) (head $($picked.head), $($picked.kind): $($picked.title)). Follow all holds and ADR 0010. If already claimed or blocked, record exact blocker on active RM board and exit cleanly."
      } elseif ($l.role -eq 'acceptance-review') { "ASSIGNED ROLE: Review any unclaimed CLEAN non-draft PR lacking exact-head independent PASS across the fleet. Preserve all holds." } else { "ASSIGNED ROLE: Claim 1 unclaimed, unblocked issue across the fleet on active RM board. Follow all holds." }

      $cmd = "& '$RdSession' start -Provider $($l.prov) -Model $($l.model) -Mode work -Lane $($l.lane) -Role $($l.role) -ExtraPrompt '$($focus -replace "'","''")'"
      $out = if ($l.host -eq 'deskcomputer') { Invoke-Expression $cmd 2>&1 | Out-String } else { $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd)); ssh -o BatchMode=yes -o ConnectTimeout=10 $l.host "powershell -NoProfile -EncodedCommand $b64" 2>&1 | Out-String }
      $lastLaunch[$l.lane] = Get-Date
      "[$(Get-Date -Format o)] RELAUNCH lane=$($l.lane) card=$(if($picked){$picked.key}else{'role-fallback'}) out=$($out.Trim() -replace '\s+',' ')" | Add-Content $LogFile
      Start-Sleep -Seconds 2
    }
  } catch { "[$(Get-Date -Format o)] LOOP_ERROR $($_.Exception.Message)" | Add-Content $LogFile }
  Start-Sleep -Seconds $PollSec
}
"[$(Get-Date -Format o)] SUPERVISOR_ENDED" | Add-Content $LogFile
