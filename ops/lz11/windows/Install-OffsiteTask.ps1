param(
    [string]$Remote = "gdrive:AI Projects/Kaspi-Store-Manager/Backups",
    [switch]$ReplaceExisting,
    [switch]$Remove,
    [switch]$ValidateOnly
)
. (Join-Path $PSScriptRoot "Common.ps1")
if ($ValidateOnly) {
    Write-Output "PASS: Install-OffsiteTask.ps1 parsed; no Windows task registered"
    return
}
if ($env:OS -ne "Windows_NT") { throw "Windows 10/11 is required" }
$taskName = "KaspiStoreManagerLZ11-OffsiteCopy"
if ($Remove) {
    $current = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $current) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "Task removed. Backups on disk and Drive were NOT deleted."
    }
    return
}
if ($Remote -notmatch "^[A-Za-z0-9_-]+:.+" -or $Remote.Contains('"')) {
    throw "Invalid rclone destination path"
}
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    throw "Install rclone and configure gdrive: for this Windows account before scheduling."
}
$remotes = @(& rclone listremotes)
if ($LASTEXITCODE -ne 0 -or ($remotes -notcontains ($Remote.Split(":")[0] + ":"))) {
    throw "Configured rclone remote was not found."
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and -not $ReplaceExisting) {
    throw "Existing task found. Use -ReplaceExisting to overwrite it explicitly."
}
$script = Join-Path $PSScriptRoot "Export-Backups.ps1"
$psExe = Join-Path $PSHOME "powershell.exe"
$arguments = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Remote "{1}"' -f $script, $Remote)
$action = New-ScheduledTaskAction -Execute $psExe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output ("Registered a current-user task: {0}, every 15 minutes while signed in." -f $taskName)
Write-Output "The task never deletes files in Drive. Check last-offsite-check.json and Task Scheduler history."
