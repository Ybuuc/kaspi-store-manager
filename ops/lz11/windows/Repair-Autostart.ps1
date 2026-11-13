param([switch]$ValidateOnly)
. (Join-Path $PSScriptRoot "Common.ps1")
if ($ValidateOnly) {
    Write-Output "PASS: Repair-Autostart.ps1 no-op, no tasks changed"
    return
}
if ($env:OS -ne "Windows_NT") { throw "Windows is required" }

Write-Host "[1/2] Migrating visible Startup CMD to hidden delayed task..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "Install-StartupTask.ps1") -ReplaceExisting
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
    # Native status is irrelevant here because we call a PowerShell script;
    # helper errors are terminating via Common.ps1.
    $global:LASTEXITCODE = 0
}
Write-Host "[2/2] Updating 15-minute Google Drive task for hidden PowerShell..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "Install-OffsiteTask.ps1") -ReplaceExisting

Write-Host "Repair complete. Both actions run hidden in Task Scheduler." -ForegroundColor Green
Write-Host "Start log: $env:LOCALAPPDATA\KaspiStoreManagerLZ11\start-last.log"
Write-Host "Data, Docker volumes and cloud backup copies were not modified."
