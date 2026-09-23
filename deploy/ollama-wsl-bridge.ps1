<#
.SYNOPSIS
    Let WSL reach a loopback-only Windows Ollama server through a narrow, reboot-safe bridge.

.DESCRIPTION
    Staff Hub runs execute inside WSL (NAT networking) and use the Windows Ollama app
    (Runner_Dashboard#1252). The secure standard (Runner_Dashboard#1257) keeps Ollama on
    127.0.0.1 and bridges only the WSL subnet to it:

      * netsh interface portproxy  <WSL adapter IP>:11434 -> 127.0.0.1:11434
      * firewall rule StaffHub-Ollama-WSL: inbound TCP 11434, LocalAddress = WSL adapter IP,
        RemoteAddress = WSL subnet, on the vEthernet (WSL*) interface only
      * scheduled task (startup, logon, every 15 min) re-running -Action Apply, because the
        WSL adapter address can change when Windows or WSL restarts

    The script never touches forwards or rules it did not create: a forward is "ours" when
    it is recorded in the state file, or when it already has exactly our shape
    (<adapter IP>:<port> -> 127.0.0.1:<port>). Anything else on our listen address is a
    conflict and the run refuses. It refuses as well while Ollama itself listens beyond
    loopback: turn off "Expose Ollama to the network" first (owner decision).

    Actions:
      Status     print the plan and exposure, change nothing (same as Apply -DryRun)
      Apply      converge the forward and firewall rule on the current adapter address
      Install    Apply, then register the scheduled task
      Uninstall  remove our forward(s), our rule and our task; backups are kept

    Needs an elevated PowerShell for Apply/Install/Uninstall.

.EXAMPLE
    # elevated
    .\ollama-wsl-bridge.ps1 -Action Status
    .\ollama-wsl-bridge.ps1 -Action Install
#>

[CmdletBinding()]
param(
    [ValidateSet('Status', 'Apply', 'Install', 'Uninstall')][string]$Action = 'Status',
    [int]$Port = 11434,
    [string]$RuleName = 'StaffHub-Ollama-WSL',
    [string]$TaskName = 'StaffHub-Ollama-WSL-Bridge',
    [string]$StateDir = (Join-Path $env:ProgramData 'RunnerDashboard\ollama-wsl-bridge'),
    [int]$WaitSeconds = 0,
    [switch]$DryRun,
    [switch]$LibraryOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── pure helpers (unit-tested in tests/deploy/test_ollama_wsl_bridge_script.py) ──

function Get-SubnetCidr {
    param([Parameter(Mandatory)][string]$IpAddress, [Parameter(Mandatory)][int]$PrefixLength)
    $bytes = ([System.Net.IPAddress]::Parse($IpAddress)).GetAddressBytes()
    # Plain arithmetic: -shl on 32-bit values overflows in Windows PowerShell 5.1.
    [uint64]$value = [uint64]$bytes[0] * 16777216 + [uint64]$bytes[1] * 65536 + [uint64]$bytes[2] * 256 + [uint64]$bytes[3]
    [uint64]$block = [uint64][math]::Pow(2, 32 - $PrefixLength)
    [uint64]$network = $value - ($value % $block)
    # Parenthesise each element: the comma operator binds tighter than %.
    $octets = @(([math]::Floor($network / 16777216) % 256), ([math]::Floor($network / 65536) % 256), ([math]::Floor($network / 256) % 256), ($network % 256))
    return ('{0}/{1}' -f ($octets -join '.'), $PrefixLength)
}

function ConvertFrom-PortProxyTable {
    param([AllowEmptyString()][string]$Text)
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
            [pscustomobject]@{
                ListenAddress  = $Matches[1]
                ListenPort     = [int]$Matches[2]
                ConnectAddress = $Matches[3]
                ConnectPort    = [int]$Matches[4]
            }
        }
    }
}

function Test-LoopbackAddress {
    param([string]$Address)
    return $Address -in @('127.0.0.1', '::1') -or $Address -like '127.*'
}

function Get-OllamaBridgePlan {
    <#
      Decide what Apply must do. Pure: all state comes in as parameters.
      Status: apply | refused | ollama-exposed | no-wsl-adapter
    #>
    param(
        [AllowEmptyString()][string]$AdapterIp,
        [int]$PrefixLength,
        [int]$Port,
        [object[]]$Forwards = @(),
        [AllowEmptyString()][string]$RecordedListen = '',
        [string[]]$OllamaListen = @()
    )
    $plan = [ordered]@{
        Status         = 'apply'
        Reason         = ''
        AdapterIp      = $AdapterIp
        Subnet         = ''
        OllamaRunning  = (@($OllamaListen).Count -gt 0)
        Actions        = @()
    }
    if ([string]::IsNullOrWhiteSpace($AdapterIp)) {
        $plan.Status = 'no-wsl-adapter'
        $plan.Reason = 'No vEthernet (WSL*) IPv4 address yet; start WSL and re-run.'
        return [pscustomobject]$plan
    }
    $exposed = @($OllamaListen | Where-Object { -not (Test-LoopbackAddress $_) })
    if ($exposed.Count -gt 0) {
        $plan.Status = 'ollama-exposed'
        $plan.Reason = "Ollama listens on $($exposed -join ', '). Turn off 'Expose Ollama to the network' (and unset OLLAMA_HOST) so it listens on 127.0.0.1 only, then re-run."
        return [pscustomobject]$plan
    }
    $plan.Subnet = Get-SubnetCidr -IpAddress $AdapterIp -PrefixLength $PrefixLength
    $onOurAddress = @($Forwards | Where-Object { $_.ListenAddress -eq $AdapterIp -and [int]$_.ListenPort -eq $Port })
    $foreign = @($onOurAddress | Where-Object { $_.ConnectAddress -ne '127.0.0.1' -or [int]$_.ConnectPort -ne $Port })
    if ($foreign.Count -gt 0) {
        $f = $foreign[0]
        $plan.Status = 'refused'
        $plan.Reason = "A forward $($f.ListenAddress):$($f.ListenPort) -> $($f.ConnectAddress):$($f.ConnectPort) already exists and is not ours; inspect it before re-running."
        return [pscustomobject]$plan
    }
    $actions = @()
    if ($RecordedListen -and $RecordedListen -ne $AdapterIp) {
        $stale = @($Forwards | Where-Object {
                $_.ListenAddress -eq $RecordedListen -and [int]$_.ListenPort -eq $Port -and
                $_.ConnectAddress -eq '127.0.0.1' -and [int]$_.ConnectPort -eq $Port })
        if ($stale.Count -gt 0) {
            $actions += [pscustomobject]@{ Op = 'remove-forward'; ListenAddress = $RecordedListen; ListenPort = $Port }
        }
    }
    if ($onOurAddress.Count -eq 0) {
        $actions += [pscustomobject]@{ Op = 'add-forward'; ListenAddress = $AdapterIp; ListenPort = $Port }
    }
    $actions += [pscustomobject]@{ Op = 'set-rule'; ListenAddress = $null; ListenPort = $null }
    $plan.Actions = $actions
    return [pscustomobject]$plan
}

if ($LibraryOnly) { return }

# ── side effects ───────────────────────────────────────────────────────────

function Get-WslAdapter {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } | Select-Object -First 1
        if ($addr) { return $addr }
        if ($WaitSeconds -gt 0) { Start-Sleep -Seconds 5 }
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Get-StatePath { Join-Path $StateDir 'state.json' }

function Read-BridgeState {
    $path = Get-StatePath
    if (Test-Path -LiteralPath $path) { return Get-Content -Raw -LiteralPath $path | ConvertFrom-Json }
    return [pscustomobject]@{ ListenAddress = ''; Port = $Port }
}

function Write-BridgeResult {
    param([object]$Plan, [string]$Outcome)
    $result = [ordered]@{
        time    = (Get-Date).ToString('o')
        action  = $Action
        dryRun  = [bool]$DryRun
        outcome = $Outcome
        plan    = $Plan
    }
    $json = $result | ConvertTo-Json -Depth 6
    if (-not $DryRun -and $Action -ne 'Status') {
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        Set-Content -LiteralPath (Join-Path $StateDir 'last-result.json') -Value $json -Encoding utf8
    }
    # Host output, not the pipeline: callers capture the returned plan object.
    Write-Host $json
}

function Invoke-Netsh {
    param([string[]]$Arguments)
    $out = & netsh.exe @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "netsh $($Arguments -join ' ') failed: $out" }
    return $out
}

function Backup-PortProxy {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Invoke-Netsh @('interface', 'portproxy', 'dump') | Set-Content -LiteralPath (Join-Path $StateDir "portproxy.bak-$stamp.txt") -Encoding utf8
}

function Set-BridgeRule {
    param([string]$AdapterIp, [string]$Subnet, [string]$InterfaceAlias)
    $existing = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
    if ($existing) {
        $portFilter = $existing | Get-NetFirewallPortFilter
        if ($existing.Direction -ne 'Inbound' -or $existing.Action -ne 'Allow' -or "$($portFilter.LocalPort)" -ne "$Port") {
            throw "Firewall rule '$RuleName' exists with a different shape; inspect it before re-running."
        }
        Set-NetFirewallRule -Name $RuleName -LocalAddress $AdapterIp -RemoteAddress $Subnet -InterfaceAlias $InterfaceAlias -Enabled True
    } else {
        New-NetFirewallRule -Name $RuleName -DisplayName 'Staff Hub Ollama from WSL only' `
            -Description 'Runner_Dashboard#1257: WSL subnet -> loopback Ollama via portproxy. Managed by deploy/ollama-wsl-bridge.ps1.' `
            -Direction Inbound -Action Allow -Enabled True -Profile Any -Protocol TCP -LocalPort $Port `
            -LocalAddress $AdapterIp -RemoteAddress $Subnet -InterfaceAlias $InterfaceAlias | Out-Null
    }
}

function Invoke-BridgeApply {
    $adapter = Get-WslAdapter
    $forwards = @(ConvertFrom-PortProxyTable -Text (Invoke-Netsh @('interface', 'portproxy', 'show', 'v4tov4')))
    $state = Read-BridgeState
    $listen = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -and (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName -like 'ollama*' } |
            ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
    $plan = Get-OllamaBridgePlan -AdapterIp $(if ($adapter) { $adapter.IPAddress } else { '' }) `
        -PrefixLength $(if ($adapter) { $adapter.PrefixLength } else { 0 }) -Port $Port `
        -Forwards $forwards -RecordedListen "$($state.ListenAddress)" -OllamaListen $listen
    if ($plan.Status -ne 'apply' -or $DryRun -or $Action -eq 'Status') {
        Write-BridgeResult -Plan $plan -Outcome $(if ($plan.Status -eq 'apply') { 'planned' } else { $plan.Status })
        return $plan
    }
    # Back up only when a forward changes: the task re-applies every 15 min and a no-op run
    # (rule refresh only) must not leave a backup file behind each time.
    if (@($plan.Actions | Where-Object { $_.Op -in @('add-forward', 'remove-forward') }).Count -gt 0) { Backup-PortProxy }
    foreach ($step in $plan.Actions) {
        switch ($step.Op) {
            'remove-forward' { Invoke-Netsh @('interface', 'portproxy', 'delete', 'v4tov4', "listenaddress=$($step.ListenAddress)", "listenport=$($step.ListenPort)") | Out-Null }
            'add-forward' { Invoke-Netsh @('interface', 'portproxy', 'add', 'v4tov4', "listenaddress=$($step.ListenAddress)", "listenport=$($step.ListenPort)", 'connectaddress=127.0.0.1', "connectport=$Port") | Out-Null }
            'set-rule' { Set-BridgeRule -AdapterIp $adapter.IPAddress -Subnet $plan.Subnet -InterfaceAlias $adapter.InterfaceAlias }
        }
    }
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    [pscustomobject]@{ ListenAddress = $adapter.IPAddress; Port = $Port; Subnet = $plan.Subnet } |
        ConvertTo-Json | Set-Content -LiteralPath (Get-StatePath) -Encoding utf8
    Write-BridgeResult -Plan $plan -Outcome 'applied'
    return $plan
}

function Register-BridgeTask {
    # The task runs as SYSTEM from a stable copy, never from a repo worktree that may be removed.
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $scriptPath = Join-Path $StateDir 'ollama-wsl-bridge.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $scriptPath -Force
    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`" -Action Apply -WaitSeconds 180"
    $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
    $triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn), $repeat)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $triggers -Principal $principal -Settings $settings `
        -Description 'Runner_Dashboard#1257: keep the WSL -> loopback Ollama bridge on the current WSL adapter address.' -Force | Out-Null
}

function Invoke-BridgeUninstall {
    $state = Read-BridgeState
    if ($DryRun) { Write-BridgeResult -Plan $state -Outcome 'would-uninstall'; return }
    Backup-PortProxy
    $forwards = @(ConvertFrom-PortProxyTable -Text (Invoke-Netsh @('interface', 'portproxy', 'show', 'v4tov4')))
    foreach ($f in $forwards) {
        if ($f.ListenAddress -eq "$($state.ListenAddress)" -and $f.ListenPort -eq $Port -and $f.ConnectAddress -eq '127.0.0.1') {
            Invoke-Netsh @('interface', 'portproxy', 'delete', 'v4tov4', "listenaddress=$($f.ListenAddress)", "listenport=$Port") | Out-Null
        }
    }
    Remove-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Get-StatePath) -ErrorAction SilentlyContinue
    Write-BridgeResult -Plan $state -Outcome 'uninstalled'
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Action -ne 'Status' -and -not $DryRun -and -not $isAdmin) {
    throw "Action $Action changes firewall/portproxy/scheduled-task settings: run from an elevated PowerShell."
}

switch ($Action) {
    'Status' { Invoke-BridgeApply | Out-Null }
    'Apply' { $plan = Invoke-BridgeApply; if ($plan.Status -notin @('apply', 'no-wsl-adapter')) { exit 2 } }
    'Install' {
        $plan = Invoke-BridgeApply
        if ($plan.Status -ne 'apply') { exit 2 }
        if (-not $DryRun) { Register-BridgeTask; Write-Host "Registered scheduled task '$TaskName'." }
    }
    'Uninstall' { Invoke-BridgeUninstall }
}
