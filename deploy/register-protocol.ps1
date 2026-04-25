# Register runner-dashboard:// custom URL protocol on Windows
# Associates the protocol with the launcher PowerShell script
# Usage: .\register-protocol.ps1
# Requires: User confirmation via Windows registry dialog

$ErrorActionPreference = "Stop"

$protocolName = "runner-dashboard"
$launcherPath = "$PSScriptRoot\launcher.ps1"
$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.$protocolName"
$urlProtocolPath = "HKCU:\Software\Classes\$protocolName"

Write-Host "Registering $protocolName:// protocol handler..."

# Create registry entry for URL protocol
if (-not (Test-Path $urlProtocolPath)) {
    New-Item -Path $urlProtocolPath -Force | Out-Null
}

# Set the protocol handler
Set-ItemProperty -Path $urlProtocolPath -Name "(Default)" -Value "URL: $protocolName Protocol"
Set-ItemProperty -Path $urlProtocolPath -Name "URL Protocol" -Value ""

# Create shell\open\command subkey
$shellPath = "$urlProtocolPath\shell\open\command"
if (-not (Test-Path $shellPath)) {
    New-Item -Path $shellPath -Force | Out-Null
}

# Set the command to call the launcher script
# PowerShell requires -Command parameter for script execution
$command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command `"& '$launcherPath'`""
Set-ItemProperty -Path $shellPath -Name "(Default)" -Value $command

Write-Host "✓ Protocol handler registered successfully"
Write-Host "When you click a runner-dashboard://start link, Windows will prompt you to allow the action."
Write-Host "Launcher script path: $launcherPath"
Write-Host "Logs will be written to: $env:USERPROFILE\.config\runner-dashboard\launcher.log"
