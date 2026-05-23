# PWA Native Launcher for Windows
# Starts backend service and opens dashboard in browser
# Called by: Windows registry handler when user clicks runner-dashboard://start URL

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    $Args
)

$ErrorActionPreference = "Stop"
$logDir = "$env:USERPROFILE\.config\runner-dashboard"
$logFile = "$logDir\launcher.log"
$healthUrl = "http://localhost:8321/health"
$dashboardUrl = "http://localhost:8321"
$maxAttempts = 10
$attemptInterval = 1

# Ensure log directory exists
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Log {
    param([string]$message)
    $timestamp = (Get-Date -Format "o")
    $logEntry = "$timestamp`tlauncher.ps1`t$message"
    Add-Content -Path $logFile -Value $logEntry
}

try {
    Log "START`tInitializing launcher"

    # Check if backend is already responding
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Log "HEALTH_CHECK`tBackend already responding, opening browser"
            Start-Process $dashboardUrl
            exit 0
        }
    } catch {
        Log "HEALTH_CHECK`tBackend not responding, will start service"
    }

    # Start the backend service
    Log "START_SERVICE`tAttempting to start runner-dashboard service"

    # Try the deployed WSL system service first. Older installs used a
    # user-level service named runner-dashboard; keep that as a fallback.
    try {
        $restartCommand = "sudo -n systemctl restart runner-dashboard.service || systemctl restart runner-dashboard.service || systemctl --user restart runner-dashboard"
        wsl.exe -d Ubuntu -e bash -lc $restartCommand
        if ($LASTEXITCODE -eq 0) {
            Log "START_SERVICE`tWSL systemd restart command sent"
        } else {
            throw "systemd restart failed with exit code $LASTEXITCODE"
        }
    } catch {
        # Try Windows Service
        Log "START_SERVICE`tSystemd not available, trying Windows Service"
        try {
            $service = Get-Service -Name "runner-dashboard" -ErrorAction SilentlyContinue
            if ($service) {
                Start-Service -Name "runner-dashboard"
                Log "START_SERVICE`tWindows Service started"
            } else {
                Log "START_SERVICE`tNo service found, attempting manual start"
                # If setup.sh exists, try running it
                $setupPath = "$PSScriptRoot\setup.sh"
                if (Test-Path $setupPath) {
                    bash $setupPath
                    Log "START_SERVICE`tRan setup.sh"
                }
            }
        } catch {
            Log "START_SERVICE`tFailed to start Windows Service: $_"
        }
    }

    # Poll health endpoint
    Log "HEALTH_CHECK`tBeginning health checks (max $maxAttempts attempts)"
    $attempt = 0
    $healthCheckPassed = $false

    while ($attempt -lt $maxAttempts) {
        $attempt++
        Start-Sleep -Seconds $attemptInterval

        try {
            $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                Log "HEALTH_CHECK`tSuccess on attempt $attempt/$maxAttempts"
                $healthCheckPassed = $true
                break
            }
        } catch {
            Log "HEALTH_CHECK`tAttempt $attempt/$maxAttempts failed: $($_.Exception.Message)"
        }
    }

    if ($healthCheckPassed) {
        Log "COMPLETE`tBackend healthy, opening dashboard"
        Start-Process $dashboardUrl
        exit 0
    } else {
        Log "COMPLETE`tFailed after $maxAttempts attempts"
        [System.Windows.Forms.MessageBox]::Show(
            "Dashboard backend failed to start. Check $logFile for details.",
            "Runner Dashboard",
            "OK",
            "Error"
        )
        exit 1
    }
} catch {
    Log "ERROR`t$($_.Exception.Message)"
    [System.Windows.Forms.MessageBox]::Show(
        "Launcher error: $($_.Exception.Message). Check $logFile for details.",
        "Runner Dashboard",
        "OK",
        "Error"
    )
    exit 1
}
