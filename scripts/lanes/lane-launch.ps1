# lane-launch.ps1 - hardened launcher for a Windows CLI-agent lane (#4421).
#
# On Windows, os.kill(pid, 0) is GenerateConsoleCtrlEvent(CTRL_C_EVENT), and a
# console CTRL+C reaches every process attached to the console: the agent CLI,
# this wrapper and every tool child. A liveness probe or a test run by the
# agent therefore interrupts the agent itself.
#
# Defence: set the CTRL+C-ignore flag (SetConsoleCtrlHandler(NULL, TRUE)) before
# starting the agent. Child processes inherit the flag, so a stray console
# CTRL+C from any descendant cannot kill the lane. Ctrl+Break and closing the
# window still work.
#
# Usage:
#   pwsh -NoProfile -File scripts/lanes/lane-launch.ps1 -Lane <name> -Exe <agent exe> `
#        [-AgentArgs <arg>] [-PromptFile <file>] [-WorkDir <dir>] [-LogDir <dir>]
param(
  [Parameter(Mandatory)][string]$Lane,
  [Parameter(Mandatory)][string]$Exe,
  [string[]]$AgentArgs = @(),
  [string]$PromptFile,
  [string]$WorkDir = (Join-Path $env:USERPROFILE 'Repositories'),
  [string]$LogDir
)
$ErrorActionPreference = 'Stop'
if (-not $LogDir) { $LogDir = Join-Path (Join-Path $env:USERPROFILE 'fleet-lanes') $Lane }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$log = Join-Path $LogDir 'relaunch.log'
function Log($m) {
  $l = '{0} {1}' -f (Get-Date -Format o), $m
  Add-Content -Path $log -Value $l -Encoding UTF8
  Write-Host $l
}
if (-not ('LaneCtrl.Native' -as [type])) {
  Add-Type -Namespace LaneCtrl -Name Native -MemberDefinition '[DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleCtrlHandler(System.IntPtr h, bool add);'
}
$ok = [LaneCtrl.Native]::SetConsoleCtrlHandler([IntPtr]::Zero, $true)
# Prompts and TUIs misbehave under TERM=dumb inherited from a remote launcher shell.
if ($env:TERM -eq 'dumb' -or -not $env:TERM) { $env:TERM = 'xterm-256color' }
$env:COLORTERM = 'truecolor'
Set-Location $WorkDir
$a = @($AgentArgs)
if ($PromptFile) { $a += (Get-Content -Raw -Path $PromptFile) }
# The prompt text is never logged; only the file name is.
Log "lane=$Lane wrapper pid=$PID ctrlc_ignore=$ok TERM=$env:TERM exe=$Exe args=$($AgentArgs -join ' ') prompt_file=$PromptFile"
try {
  & $Exe @a
  Log "agent exited LASTEXITCODE=$LASTEXITCODE"
} catch { Log "agent launch error: $_" }
finally { Log 'wrapper end' }
