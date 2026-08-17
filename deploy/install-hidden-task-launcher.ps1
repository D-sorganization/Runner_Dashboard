<#
.SYNOPSIS
    Rewrite a scheduled task's action to launch through run-hidden.vbs so it
    never opens a console window in the interactive session.

.DESCRIPTION
    Interactive-token scheduled tasks whose action is a console executable
    (powershell.exe, cmd.exe, bash.exe) pop a visible console window each time
    they fire and steal foreground focus. "-WindowStyle Hidden" is not enough:
    the console host window is created before the argument is parsed, so a
    focus-stealing flash remains. This installer rewrites the task action from

        <exe> <args>
    to
        wscript.exe //B //Nologo "<run-hidden.vbs>" <exe> <args>

    which never creates a console window. Triggers, settings, and principal
    are untouched. The rewrite is idempotent (wrapping a wrapped task is a
    no-op) and reversible (-Revert restores the original action verbatim).

    S4U / SYSTEM tasks do not need this (they run in a non-interactive
    session with no desktop); it exists for tasks that must stay
    InteractiveToken, e.g. anything that has to reach the user's WSL session.

.EXAMPLE
    .\install-hidden-task-launcher.ps1 -TaskName 'RunnerFleet-Health-Monitor'

.EXAMPLE
    .\install-hidden-task-launcher.ps1 -TaskName 'RunnerFleet-Health-Monitor' -Revert
#>

[CmdletBinding()]
param(
    [string]$TaskName = '',
    # Defaults resolved in the body: $PSScriptRoot is empty during param
    # binding when the script is dot-sourced under Windows PowerShell 5.1.
    [string]$VbsPath = '',
    [string]$WScriptPath = '',
    [switch]$Revert,
    [switch]$DryRun,
    # Dot-source with -FunctionsOnly to expose the pure helpers for tests
    # without touching the Task Scheduler.
    [switch]$FunctionsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($WScriptPath)) {
    $systemRoot = if ($env:SystemRoot) { $env:SystemRoot } else { 'C:\Windows' }
    $WScriptPath = Join-Path $systemRoot 'System32\wscript.exe'
}
if ([string]::IsNullOrWhiteSpace($VbsPath) -and -not $FunctionsOnly) {
    $VbsPath = Join-Path $PSScriptRoot 'run-hidden.vbs'
}

# ---------------------------------------------------------------------------
# Pure helpers (no Task Scheduler access; covered by tests/deploy/)
# ---------------------------------------------------------------------------

function Format-CommandToken {
    <# Quote a single command-line token if it needs it. Embedded double
       quotes cannot be re-quoted losslessly once WScript strips them, so
       they violate the launcher's argument contract. #>
    param([Parameter(Mandatory)][string]$Token)
    if ($Token.Contains('"')) {
        throw "token contains an embedded double quote and cannot be wrapped losslessly: $Token"
    }
    if ($Token.Contains(' ')) { return '"' + $Token + '"' }
    return $Token
}

function Get-WrappedArgumentPrefix {
    param([Parameter(Mandatory)][string]$VbsPath)
    # //B suppresses script-host error dialogs; //Nologo keeps stdout clean.
    return '//B //Nologo "' + $VbsPath + '" '
}

function Test-WrappedAction {
    <# True when the action already launches through this VbsPath. #>
    param(
        [Parameter(Mandatory)][string]$Execute,
        [AllowEmptyString()][string]$Arguments = '',
        [Parameter(Mandatory)][string]$VbsPath
    )
    $leaf = Split-Path -Leaf ($Execute.Trim('"'))
    if ($leaf -ine 'wscript.exe') { return $false }
    $prefix = Get-WrappedArgumentPrefix -VbsPath $VbsPath
    return ([string]$Arguments).StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function ConvertTo-WrappedAction {
    <# Build the wscript-wrapped equivalent of an action.
       Preconditions: Execute non-empty, not already wrapped.
       Postcondition: result launches wscript.exe with the original command
       appended verbatim after the VBS path, so ConvertFrom-WrappedAction can
       restore it exactly. #>
    param(
        [Parameter(Mandatory)][string]$Execute,
        [AllowEmptyString()][string]$Arguments = '',
        [Parameter(Mandatory)][string]$VbsPath,
        [string]$WScriptPath = (Join-Path $env:SystemRoot 'System32\wscript.exe')
    )
    if ([string]::IsNullOrWhiteSpace($Execute)) {
        throw 'Execute must be a non-empty string'
    }
    if (Test-WrappedAction -Execute $Execute -Arguments $Arguments -VbsPath $VbsPath) {
        throw "action is already wrapped by $VbsPath; refusing to double-wrap"
    }
    $command = Format-CommandToken -Token ($Execute.Trim('"'))
    if (-not [string]::IsNullOrEmpty($Arguments)) {
        $command = $command + ' ' + $Arguments
    }
    return [pscustomobject]@{
        Execute   = $WScriptPath
        Arguments = (Get-WrappedArgumentPrefix -VbsPath $VbsPath) + $command
    }
}

function ConvertFrom-WrappedAction {
    <# Restore the original action from a wrapped one (the -Revert path).
       Precondition: the action was wrapped with this VbsPath.
       Postcondition: returns the original Execute/Arguments verbatim. #>
    param(
        [Parameter(Mandatory)][string]$Execute,
        [AllowEmptyString()][string]$Arguments = '',
        [Parameter(Mandatory)][string]$VbsPath
    )
    if (-not (Test-WrappedAction -Execute $Execute -Arguments $Arguments -VbsPath $VbsPath)) {
        throw "action is not wrapped by $VbsPath; nothing to revert"
    }
    $prefix = Get-WrappedArgumentPrefix -VbsPath $VbsPath
    $remainder = ([string]$Arguments).Substring($prefix.Length)
    if ($remainder.StartsWith('"')) {
        $closing = $remainder.IndexOf('"', 1)
        if ($closing -lt 0) { throw "malformed wrapped action (unterminated quote): $remainder" }
        $originalExecute = $remainder.Substring(1, $closing - 1)
        $originalArguments = $remainder.Substring($closing + 1).TrimStart(' ')
    }
    else {
        $space = $remainder.IndexOf(' ')
        if ($space -lt 0) {
            $originalExecute = $remainder
            $originalArguments = ''
        }
        else {
            $originalExecute = $remainder.Substring(0, $space)
            $originalArguments = $remainder.Substring($space + 1)
        }
    }
    if ([string]::IsNullOrWhiteSpace($originalExecute)) {
        throw "malformed wrapped action (empty original executable): $remainder"
    }
    return [pscustomobject]@{
        Execute   = $originalExecute
        Arguments = $originalArguments
    }
}

if ($FunctionsOnly) { return }

# ---------------------------------------------------------------------------
# Main flow (Task Scheduler access lives only below this line)
# ---------------------------------------------------------------------------

if ([string]::IsNullOrWhiteSpace($TaskName)) {
    throw 'TaskName must be a non-empty string'
}
if (-not (Test-Path -LiteralPath $VbsPath -PathType Leaf)) {
    throw "launcher script not found: $VbsPath (deploy run-hidden.vbs first)"
}
if (-not (Test-Path -LiteralPath $WScriptPath -PathType Leaf)) {
    throw "wscript.exe not found at $WScriptPath"
}

# Get-ScheduledTask -TaskName searches every folder; Set-ScheduledTask
# without -TaskPath looks only at the root (0x80070002 for foldered tasks),
# so pin the discovered path for the write, the re-read, and the fallback.
$task = Get-ScheduledTask -TaskName $TaskName
if (@($task).Count -gt 1) {
    $paths = (@($task) | ForEach-Object { $_.TaskPath }) -join ', '
    throw "'$TaskName' matches multiple tasks ($paths); disambiguation is not supported"
}
$taskPath = [string]$task.TaskPath
if (@($task.Actions).Count -ne 1) {
    throw "task '$TaskName' has $(@($task.Actions).Count) actions; only single-action tasks are supported"
}
$currentExecute = [string]$task.Actions[0].Execute
$currentArguments = [string]$task.Actions[0].Arguments
if ([string]::IsNullOrWhiteSpace($currentExecute)) {
    throw "task '$TaskName' has an empty action executable"
}

$alreadyWrapped = Test-WrappedAction -Execute $currentExecute -Arguments $currentArguments -VbsPath $VbsPath
if ($Revert) {
    $target = ConvertFrom-WrappedAction -Execute $currentExecute -Arguments $currentArguments -VbsPath $VbsPath
    $mode = 'revert'
}
elseif ($alreadyWrapped) {
    $target = $null
    $mode = 'noop-already-wrapped'
}
else {
    $target = ConvertTo-WrappedAction -Execute $currentExecute -Arguments $currentArguments `
        -VbsPath $VbsPath -WScriptPath $WScriptPath
    $mode = 'wrap'
}

$result = [ordered]@{
    task_name      = $TaskName
    mode           = $mode
    from_execute   = $currentExecute
    from_arguments = $currentArguments
    to_execute     = if ($target) { $target.Execute } else { $currentExecute }
    to_arguments   = if ($target) { $target.Arguments } else { $currentArguments }
    write_path     = $null
    verified       = $null
}

if ($DryRun -or $mode -eq 'noop-already-wrapped') {
    [pscustomobject]$result | ConvertTo-Json -Depth 3
    exit 0
}

try {
    $newAction = New-ScheduledTaskAction -Execute $target.Execute -Argument $target.Arguments
    Set-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -Action $newAction | Out-Null
    $result.write_path = 'Set-ScheduledTask'
}
catch {
    # Non-elevated sessions can lack CIM write access to their own tasks;
    # schtasks /Change works there but its /TR value is silently truncated
    # beyond 261 characters -- refuse rather than corrupt the task.
    $tr = (Format-CommandToken -Token $target.Execute) + ' ' + $target.Arguments
    $trEscaped = $tr.Replace('"', '\"')
    if ($tr.Length -gt 261) {
        throw ("Set-ScheduledTask failed ($($_.Exception.Message)) and the schtasks fallback " +
            "cannot carry $($tr.Length) chars (limit 261). Re-run elevated.")
    }
    $p = Start-Process -FilePath 'schtasks.exe' `
        -ArgumentList ('/Change /TN "' + ($taskPath + $TaskName) + '" /TR "' + $trEscaped + '"') `
        -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        throw "schtasks /Change fallback failed with exit code $($p.ExitCode)"
    }
    $result.write_path = 'schtasks /Change'
}

# Postcondition: what we wrote is what the task now runs. schtasks /TR folds
# executable and arguments into one command line, so compare that form.
$reread = Get-ScheduledTask -TaskName $TaskName -TaskPath $taskPath
$rereadExecute = ([string]$reread.Actions[0].Execute).Trim('"')
$rereadArguments = [string]$reread.Actions[0].Arguments
$expectedCommand = (Format-CommandToken -Token $target.Execute) + ' ' + $target.Arguments
$actualCommand = (Format-CommandToken -Token $rereadExecute) + ' ' + $rereadArguments
$result.verified = ($actualCommand -eq $expectedCommand)
if (-not $result.verified) {
    throw ("postcondition failed for '$TaskName': task action is '$actualCommand', " +
        "expected '$expectedCommand'")
}

[pscustomobject]$result | ConvertTo-Json -Depth 3
