<#
Owner runs -Install from elevated PowerShell. -DryRun never writes host state.
The SYSTEM task discovers an existing WSL adapter; it does not launch a distro.
Startup/logon plus a five-minute retry cover WSL starting after Windows logon.
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$DryRun,
    [switch]$AdoptExisting,
    # Synthetic inventory is accepted ONLY in read-only mode (used by tests).
    [string]$SnapshotPath
)
$ErrorActionPreference = 'Stop'
$owner = 'RunnerDashboard.OllamaWslBridge.v1'
$ruleName = 'StaffHub-Ollama-WSL'
$taskName = 'StaffHub-Ollama-WSL-Bridge'
$root = Join-Path $env:ProgramData 'RunnerDashboard\OllamaWslBridge'
$statePath = Join-Path $root 'state.json'

function Get-Subnet([string]$Address, [int]$Prefix) {
    if ($Prefix -lt 1 -or $Prefix -gt 32) { throw 'Invalid WSL prefix.' }
    $bytes = [Net.IPAddress]::Parse($Address).GetAddressBytes()
    if ($bytes.Length -ne 4) { throw 'WSL must use IPv4 NAT.' }
    $network = for ($i = 0; $i -lt 4; $i++) {
        $bits = [Math]::Min(8, [Math]::Max(0, $Prefix - 8 * $i))
        $bytes[$i] -band (256 - [Math]::Pow(2, 8 - $bits))
    }
    return ($network -join '.') + '/' + $Prefix
}

function Get-Inventory {
    $adapters = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        $_.InterfaceAlias -like 'vEthernet (WSL*' -and $_.AddressState -eq 'Preferred'
    } | ForEach-Object {
        @{ address = $_.IPAddress; prefix = $_.PrefixLength; alias = $_.InterfaceAlias }
    })
    $forwards = @(netsh interface portproxy show v4tov4 | ForEach-Object {
        if ($_ -match '^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
            @{ address = $Matches[1]; port = [int]$Matches[2]; target = $Matches[3]; targetPort = [int]$Matches[4] }
        }
    })
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect portproxy.' }
    $rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    $ruleData = $null
    if ($rule) {
        $address = $rule | Get-NetFirewallAddressFilter
        $port = $rule | Get-NetFirewallPortFilter
        $interface = $rule | Get-NetFirewallInterfaceFilter
        $ruleData = @{ owner = $rule.Description; address = ($address.LocalAddress -join ',');
            subnet = ($address.RemoteAddress -join ','); alias = ($interface.InterfaceAlias -join ',');
            tcp = ($port.Protocol -in @('TCP', '6')); port = $port.LocalPort;
            inbound = ($rule.Direction -eq 'Inbound'); allow = ($rule.Action -eq 'Allow'); enabled = ($rule.Enabled -eq 'True') }
    }
    $broad = @(Get-NetFirewallApplicationFilter | Where-Object { $_.Program -match '(?i)[\\/]ollama\.exe$' } |
        Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } |
        ForEach-Object { if (($_ | Get-NetFirewallAddressFilter).RemoteAddress -contains 'Any') { $_.Name } })
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $state = if (Test-Path -LiteralPath $statePath) { Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } else { $null }
    return @{ adapters = $adapters; forwards = $forwards; rule = $ruleData; state = $state;
        task = $(if ($task) { @{ owner = $task.Description } } else { $null }); broadRules = $broad;
        listeners = @(Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalAddress -Unique);
        hostValues = @([Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'User'),
            [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'Machine')) }
}

function ConvertTo-Cidr([string]$Value) {
    if ($Value -match '^([^/]+)/([0-9.]+)$' -and $Matches[2].Contains('.')) {
        $address = $Matches[1]
        $mask = [Net.IPAddress]::Parse($Matches[2]).GetAddressBytes()
        $bits = ($mask | ForEach-Object { [Convert]::ToString($_, 2).PadLeft(8, '0') }) -join ''
        if ($bits -notmatch '^1+0*$') { throw 'Invalid firewall subnet mask.' }
        return Get-Subnet $address ($bits.TrimEnd('0').Length)
    }
    return $Value
}

function Get-Plan($Inventory) {
    if ($Install -and $Uninstall) { throw 'Choose Install or Uninstall.' }
    if ($Inventory.state -and $Inventory.state.owner -ne $owner) { throw 'Foreign bridge state; refusing.' }
    if ($Inventory.task -and $Inventory.task.owner -ne $owner) { throw 'Foreign scheduled task; refusing.' }
    if ($Inventory.rule) { $Inventory.rule.subnet = ConvertTo-Cidr $Inventory.rule.subnet }
    $adapter = $null
    $subnet = $null
    if (@($Inventory.adapters).Count -gt 1) { throw 'Multiple WSL adapters; refusing ambiguous routing.' }
    if (@($Inventory.adapters).Count -eq 1) {
        $adapter = $Inventory.adapters[0]
        $subnet = Get-Subnet $adapter.address $adapter.prefix
    }
    $legacy = $false
    if ($Inventory.rule -and $Inventory.rule.owner -ne $owner) {
        $r = $Inventory.rule
        $legacy = $AdoptExisting -and $adapter -and -not $r.owner -and
            $r.address -eq $adapter.address -and $r.subnet -eq $subnet -and
            $r.alias -eq $adapter.alias -and $r.tcp -and $r.port -eq 11434 -and $r.inbound -and $r.allow
        if (-not $legacy) { throw 'Foreign firewall rule; refusing. Legacy adoption requires an exact narrow rule.' }
    }
    $ownedAddresses = @()
    if ($Inventory.state) { $ownedAddresses += $Inventory.state.address; $ownedAddresses += @($Inventory.state.previousAddresses) }
    if ($legacy) { $ownedAddresses += $Inventory.rule.address }
    $remove = @()
    $existing = $false
    foreach ($forward in $Inventory.forwards) {
        if ($forward.port -ne 11434) { continue }
        $ours = $forward.address -in $ownedAddresses -and $forward.target -eq '127.0.0.1' -and $forward.targetPort -eq 11434
        if (-not $ours) { throw 'Foreign port 11434 forward; refusing to modify networking.' }
        if ($Uninstall -or ($adapter -and $forward.address -ne $adapter.address)) {
            $remove += $forward.address
        } elseif ($adapter -and $forward.address -eq $adapter.address) { $existing = $true }
    }
    if (-not $Uninstall) {
        foreach ($value in $Inventory.hostValues) {
            if ($value -and $value -notmatch '^(https?://)?(127\.0\.0\.1|localhost)(:11434)?/?$') {
                throw 'OLLAMA_HOST is not loopback. Owner must disable expose-network and restart Ollama first.'
            }
        }
        foreach ($address in $Inventory.listeners) {
            if ($address -notin @('127.0.0.1', '::1') -and $address -notin $ownedAddresses) {
                throw 'Non-loopback Ollama listener. Owner must disable expose-network and restart Ollama first.'
            }
        }
    }
    return [ordered]@{ status = $(if (-not $adapter -and -not $Uninstall) { 'waiting-for-wsl' } else { 'planned' });
        address = $(if ($adapter) { $adapter.address } else { $null }); subnet = $subnet;
        alias = $(if ($adapter) { $adapter.alias } else { $null });
        removeForwards = @($remove); addForward = [bool]($adapter -and -not $existing -and -not $Uninstall);
        disableRule = [bool]($Uninstall -and $Inventory.rule); disableTask = [bool]($Uninstall -and $Inventory.task);
        disableBroadRules = @($(if (-not $Uninstall) { $Inventory.broadRules }));
        registerTask = [bool]$Install;
        unchanged = [bool](-not $Install -and -not $Uninstall -and $existing -and $remove.Count -eq 0 -and
            @($Inventory.broadRules).Count -eq 0 -and $Inventory.rule -and $Inventory.rule.owner -eq $owner -and
            $Inventory.rule.enabled -and $Inventory.rule.address -eq $adapter.address -and
            $Inventory.rule.subnet -eq $subnet -and $Inventory.rule.alias -eq $adapter.alias -and
            $Inventory.rule.tcp -and $Inventory.rule.port -eq 11434 -and $Inventory.rule.inbound -and $Inventory.rule.allow) }
}

function Invoke-Netsh([string[]]$Arguments) {
    $output = & netsh @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "netsh failed: $output" }
}

try {
    if ($SnapshotPath -and -not $DryRun) { throw 'SnapshotPath requires DryRun.' }
    $inventory = if ($SnapshotPath) { Get-Content -Raw -LiteralPath $SnapshotPath | ConvertFrom-Json } else { Get-Inventory }
    $plan = Get-Plan $inventory
    if ($DryRun) { $plan | ConvertTo-Json -Depth 8; exit 0 }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Owner must run installation from elevated PowerShell.'
    }
    # Do not change a working bridge while WSL is absent. The installed task retries.
    if (($plan.status -eq 'waiting-for-wsl' -and -not $Install) -or $plan.unchanged) { $plan | ConvertTo-Json -Depth 8; exit 0 }
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    # Only administrators/SYSTEM may edit the script which the highest task runs.
    & icacls $root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot secure task directory.' }
    $stamp = Get-Date -Format 'yyyy-MM-dd-HHmmss-ffff'
    netsh interface portproxy dump | Set-Content -LiteralPath (Join-Path $root "portproxy.bak-$stamp.txt")
    if ($LASTEXITCODE -ne 0) { throw 'Cannot back up portproxy.' }
    $inventory | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $root "inventory.bak-$stamp.json")
    if ($inventory.task) { Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $root "task.bak-$stamp.xml") }
    if (Test-Path -LiteralPath $statePath) { Copy-Item -LiteralPath $statePath -Destination "$statePath.bak-$stamp" }
    # Export complete rule/filter objects for owner rollback, then disable (never delete).
    $backupRules = @($inventory.broadRules) + @($(if ($inventory.rule) { $ruleName }))
    foreach ($name in $backupRules) {
        $rule = Get-NetFirewallRule -Name $name
        @{ rule = $rule; address = @($rule | Get-NetFirewallAddressFilter);
            port = @($rule | Get-NetFirewallPortFilter); application = @($rule | Get-NetFirewallApplicationFilter);
            interface = @($rule | Get-NetFirewallInterfaceFilter) } |
            Export-Clixml -LiteralPath (Join-Path $root ("rule-" + [Guid]::NewGuid() + ".bak-$stamp.xml"))
    }
    foreach ($name in $plan.disableBroadRules) { Disable-NetFirewallRule -Name $name | Out-Null }
    if ($Uninstall) {
        if ($plan.disableRule) { Disable-NetFirewallRule -Name $ruleName | Out-Null }
        if ($plan.disableTask) { Disable-ScheduledTask -TaskName $taskName | Out-Null }
    } elseif ($plan.status -ne 'waiting-for-wsl') {
        $parameters = @{ Direction = 'Inbound'; Action = 'Allow'; Enabled = 'True'; Profile = 'Any';
            Protocol = 'TCP'; LocalAddress = $plan.address; LocalPort = 11434; RemoteAddress = $plan.subnet;
            InterfaceAlias = $plan.alias; Description = $owner }
        if ($inventory.rule) { Set-NetFirewallRule -Name $ruleName @parameters | Out-Null }
        else { New-NetFirewallRule -Name $ruleName -DisplayName 'Staff Hub Ollama from WSL only' @parameters | Out-Null }
        # Write ownership before adding the forward, so interrupted installs are recoverable.
        @{ owner = $owner; address = $plan.address; previousAddresses = @($plan.removeForwards) } | ConvertTo-Json | Set-Content -LiteralPath $statePath
        if ($plan.addForward) {
            Invoke-Netsh -Arguments @('interface', 'portproxy', 'add', 'v4tov4', "listenaddress=$($plan.address)", 'listenport=11434', 'connectaddress=127.0.0.1', 'connectport=11434')
        }
    }
    foreach ($address in $plan.removeForwards) {
        Invoke-Netsh -Arguments @('interface', 'portproxy', 'delete', 'v4tov4', "listenaddress=$address", 'listenport=11434')
    }
    if ($Install) {
        $installedScript = Join-Path $root 'ollama-wsl-bridge.ps1'
        if ($PSCommandPath -ne $installedScript) {
            if (Test-Path -LiteralPath $installedScript) { Copy-Item -LiteralPath $installedScript -Destination "$installedScript.bak-$stamp" }
            Copy-Item -LiteralPath $PSCommandPath -Destination $installedScript
        }
        # SYSTEM does not inherit the owner's CurrentUser policy. Scope this to the task process.
        $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File `"$installedScript`""
        $triggers = @(New-ScheduledTaskTrigger -AtStartup; New-ScheduledTaskTrigger -AtLogOn;
            New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Description $owner -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
    }
    $plan.status = $(if ($Uninstall) { 'uninstalled' } elseif ($plan.status -eq 'waiting-for-wsl') { 'waiting-for-wsl' } else { 'configured' })
    $resultPath = Join-Path $root 'result.json'
    if (Test-Path -LiteralPath $resultPath) { Copy-Item -LiteralPath $resultPath -Destination "$resultPath.bak-$stamp" }
    $plan | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath
    $plan | ConvertTo-Json -Depth 8
} catch {
    @{ status = 'refused'; error = $_.Exception.Message } | ConvertTo-Json
    exit 1
}
