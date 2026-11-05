<#
.SYNOPSIS
    Idempotent per-agent fleet token provisioning (Runner_Dashboard#3895 slice 5).

.DESCRIPTION
    Mints a bot token for a principal via the admin API (docs/agents/connect.md
    "Mint a Per-Agent Token") and writes it to the identity directory's
    agent-tokens\ folder, unless a non-empty token file is already there.
    Never prints or logs the token itself.

.PARAMETER Principal
    Principal id, e.g. agent-codex.

.PARAMETER ApiUrl
    Dashboard base URL. Defaults to $env:FLEET_API_URL, else http://127.0.0.1:8321.

.PARAMETER IdentityDir
    Identity directory. Defaults to $env:DASHBOARD_IDENTITY_DIR, else
    $env:USERPROFILE\.config\runner-dashboard.

.PARAMETER TokenName
    Human-readable name recorded with the token. Defaults to "<Principal> on <hostname>".

.PARAMETER ExpiresInDays
    Token lifetime in days. Default 90.

.PARAMETER Force
    Re-mint even if a token file already exists.

.PARAMETER DryRun
    Print the planned action without minting or writing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Principal,

    [string]$ApiUrl = $(if ($env:FLEET_API_URL) { $env:FLEET_API_URL } else { "http://127.0.0.1:8321" }),

    [string]$IdentityDir = $(if ($env:DASHBOARD_IDENTITY_DIR) { $env:DASHBOARD_IDENTITY_DIR } else { Join-Path $env:USERPROFILE ".config\runner-dashboard" }),

    [string]$TokenName,

    [int]$ExpiresInDays = 90,

    [switch]$Force,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-SetupLog {
    param([string]$Message)
    Write-Host "Setup-AgentToken: $Message"
}

function Fail {
    param([string]$Message)
    Write-Error "Setup-AgentToken: $Message"
    exit 2
}

if ($ExpiresInDays -le 0) {
    Fail "-ExpiresInDays must be a positive integer"
}

if (-not $TokenName) {
    $TokenName = "$Principal on $($env:COMPUTERNAME)"
}

$TokenDir = Join-Path $IdentityDir "agent-tokens"
$TokenFile = Join-Path $TokenDir "$Principal.token"

if ((Test-Path $TokenFile) -and (-not $Force)) {
    $existing = Get-Content -Path $TokenFile -Raw -ErrorAction SilentlyContinue
    if ($existing -and $existing.Trim().Length -gt 0) {
        Write-SetupLog "token file already present for $Principal ($TokenFile); use -Force to re-mint"
        exit 0
    }
}

if ($DryRun) {
    Write-SetupLog "[dry-run] would POST $ApiUrl/api/admin/principals/$Principal/token and write $TokenFile"
    exit 0
}

New-Item -ItemType Directory -Path $TokenDir -Force | Out-Null

$body = @{ name = $TokenName; expires_in_days = $ExpiresInDays } | ConvertTo-Json -Compress
$headers = @{ "X-Requested-With" = "XMLHttpRequest"; "Content-Type" = "application/json" }
$mintUrl = "$ApiUrl/api/admin/principals/$Principal/token"

try {
    $response = Invoke-RestMethod -Method Post -Uri $mintUrl -Headers $headers -Body $body -TimeoutSec 15
} catch {
    Fail "mint request for $Principal failed: $($_.Exception.Message)"
}

$tokenValue = $response.token
if (-not $tokenValue) {
    Fail "mint response for $Principal did not include a token"
}

$tmpFile = Join-Path $TokenDir ".$Principal.token.$([System.Guid]::NewGuid().ToString('N'))"
try {
    [System.IO.File]::WriteAllText($tmpFile, $tokenValue, [System.Text.Encoding]::ASCII)
    Move-Item -Path $tmpFile -Destination $TokenFile -Force

    if ($IsWindows -or $null -eq $IsWindows) {
        try {
            $acl = Get-Acl $TokenFile
            $acl.SetAccessRuleProtection($true, $false)
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
                "FullControl", "Allow")
            $acl.SetAccessRule($rule)
            Set-Acl -Path $TokenFile -AclObject $acl
        } catch {
            Write-SetupLog "warning: could not restrict ACL on $TokenFile ($($_.Exception.Message))"
        }
    }
} finally {
    if (Test-Path $tmpFile) {
        Remove-Item -Path $tmpFile -Force -ErrorAction SilentlyContinue
    }
    $tokenValue = $null
}

Write-SetupLog "wrote token for $Principal to $TokenFile"
