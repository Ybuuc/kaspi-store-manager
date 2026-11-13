param(
    [switch]$ReplaceExisting,
    [switch]$Remove,
    [switch]$ValidateOnly
)
. (Join-Path $PSScriptRoot "Common.ps1")

if ($ValidateOnly) {
    Write-Output "PASS: Install-StartupTask.ps1 no-op, no scheduled task or Startup modification"
    return
}
if ($env:OS -ne "Windows_NT") { throw "Windows is required" }
$taskName = "KaspiStoreManagerLZ11-Start"
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "KaspiStoreManagerLZ11.cmd"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($Remove) {
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "Disabled Kaspi Store Manager scheduled startup."
    }
    if (Test-Path -LiteralPath $startup -PathType Leaf) {
        Rename-Item -LiteralPath $startup -NewName "KaspiStoreManagerLZ11.cmd.disabled" -Force
        Write-Output "Disabled legacy startup CMD."
    }
    return
}
if ($null -ne $existing -and -not $ReplaceExisting) {
    throw "Task exists. Specify -ReplaceExisting to update it."
}

$script = Join-Path $PSScriptRoot "Start-LZ11.ps1"
if (-not (Test-Path -LiteralPath $script)) { throw "Start-LZ11.ps1 not found." }
$psExe = Join-Path $PSHOME "powershell.exe"
$arguments = ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -QuietStartup' -f $script)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $psExe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
# Allow Docker Desktop time to start after Windows sign-in.
$trigger.Delay = "PT1M"
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

# Migrate previous visible login-startup CMD only after registration succeeded.
if (Test-Path -LiteralPath $startup -PathType Leaf) {
    Rename-Item -LiteralPath $startup -NewName "KaspiStoreManagerLZ11.cmd.disabled" -Force
}
Write-Output "Hidden scheduled login startup installed (1-minute delay)."
Write-Output ("Task: {0}; log: {1}" -f $taskName, (Join-Path (Get-KsmDataDir) "start-last.log"))
