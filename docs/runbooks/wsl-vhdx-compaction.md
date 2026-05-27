# Runbook: WSL VHDX Compaction

WSL 2 virtual hard disk files (`ext4.vhdx`) grow dynamically but do not automatically shrink when files are deleted inside WSL. This runbook describes the safe compact flow to reclaim host disk space without risking data corruption or hitting sharing conflicts.

## Symptom

- The host Windows drive (usually `C:`) is running low on space.
- The WSL partition size inside Linux is small (e.g. 20GB used), but the `ext4.vhdx` file on the Windows host is massive (e.g. 100GB).
- Storage health alerts or the Diagnostics tab warn of high Windows disk usage.

## Severity

**P3** - Low urgency maintenance task. However, if the host drive runs completely out of space, it can cause database corruption and VM crashes (**P1**).

## Pre-requisites

- Windows PowerShell (run as Administrator).
- Access to the host machine running the runner dashboard.

---

## Safe Compact Flow

Follow these steps in order to avoid locking issues (`ERROR_SHARING_VIOLATION`) or data corruption.

### Step 1: Disable Monitor Tasks

To prevent automated watchdogs or keepalive scripts from spawning new WSL processes during compaction, temporarily disable the scheduled keepalive task.

In PowerShell (Admin):

```powershell
Disable-ScheduledTask -TaskName "WSL-Dashboard-Keepalive" -ErrorAction SilentlyContinue
```

### Step 2: Stop WSL and Services

Ensure all WSL instances and associated Windows services are completely stopped.

In PowerShell (Admin):

```powershell
# Shutdown all running WSL instances
wsl --shutdown

# Stop the WSL manager service
Stop-Service -Name "LxssManager" -Force
```

Confirm that no `vmwp.exe` (Virtual Machine Worker Process) is running:

```powershell
Get-Process -Name "vmwp" -ErrorAction SilentlyContinue
```

If processes are still returned, wait a few seconds or terminate them.

### Step 3: Dismount/Detach the Disk Image

Before compacting, ensure the virtual disk is fully dismounted from the host OS loopback controller.

You can use the built-in `diskpart` utility:

```powershell
# Create a temporary script for diskpart
@'
select vdisk file="C:\WSL\ext4.vhdx"
detach vdisk
'@ | Out-File -FilePath "$env:TEMP\detach.txt" -Encoding ascii

# Run diskpart to detach the disk
diskpart /s "$env:TEMP\detach.txt"
Remove-Item -Path "$env:TEMP\detach.txt"
```

_(Replace `C:\WSL\ext4.vhdx` with the actual path to your distribution's VHDX file, which can be found in the Diagnostics tab.)_

### Step 4: Compact the VHDX

Compact the virtual disk to reclaim unused space.

**Method A: Diskpart (Recommended, works on all Windows editions)**

```powershell
# Create a temporary script for diskpart
@'
select vdisk file="C:\WSL\ext4.vhdx"
compact vdisk
'@ | Out-File -FilePath "$env:TEMP\compact.txt" -Encoding ascii

# Run diskpart to compact the disk
diskpart /s "$env:TEMP\compact.txt"
Remove-Item -Path "$env:TEMP\compact.txt"
```

**Method B: PowerShell (Requires Hyper-V Module)**

```powershell
# Mount read-only first
Mount-VHD -Path "C:\WSL\ext4.vhdx" -ReadOnly
# Compact
Optimize-VHD -Path "C:\WSL\ext4.vhdx" -Mode Full
# Dismount
Dismount-VHD -Path "C:\WSL\ext4.vhdx"
```

### Step 5: Restart WSL Services

Bring the WSL subsystem and services back online.

In PowerShell (Admin):

```powershell
Start-Service -Name "LxssManager"
```

### Step 6: Re-enable Monitor Tasks

Resume normal monitoring of the WSL dashboard instance.

In PowerShell (Admin):

```powershell
Enable-ScheduledTask -TaskName "WSL-Dashboard-Keepalive"
# Manually trigger the task to verify restart
Start-ScheduledTask -TaskName "WSL-Dashboard-Keepalive"
```

---

## Troubleshooting

### ERROR_SHARING_VIOLATION (0x80070020)

If `diskpart` or `Get-DiskImage`/`Optimize-VHD` fails with:
`The process cannot access the file because it is being used by another process.`

This indicates a process (usually `vmwp.exe` or Docker Desktop) is still locking the VHDX.

1. Run `Get-Process | Where-Object {$_.Path -like "*vhdx*"}`, or use Sysinternals Process Explorer to search for handles matching `ext4.vhdx`.
2. Stop the Docker Desktop service if running.
3. Terminate any stray `vmwp.exe` processes: `Stop-Process -Name "vmwp" -Force`.
4. Re-run Step 3 (Dismount) and Step 4 (Compact).
