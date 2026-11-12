param(
    [string]$Remote = "gdrive:AI Projects/Kaspi-Store-Manager/Backups",
    [switch]$ValidateOnly
)
. (Join-Path $PSScriptRoot "Common.ps1")

if ($ValidateOnly) {
    Write-Output "PASS: Export-Backups.ps1 parsed; no Docker or rclone commands executed"
    return
}
if ($env:OS -ne "Windows_NT") { throw "Windows 10/11 is required" }
if ($Remote -notmatch "^[A-Za-z0-9_-]+:.+" -or $Remote.Contains('"')) {
    throw "Remote must be an existing rclone remote path (for example gdrive:AI Projects/Kaspi-Store-Manager/Backups)"
}
$null = Get-Command docker -ErrorAction Stop
$null = Get-Command rclone -ErrorAction Stop

$remoteName = $Remote.Split(":")[0] + ":"
$remotes = @(& rclone listremotes)
if ($LASTEXITCODE -ne 0 -or ($remotes -notcontains $remoteName)) {
    throw ("rclone remote {0} is missing. Run rclone config first." -f $remoteName)
}

$repo = Get-KsmRepoRoot
$compose = Join-Path $repo "ops\lz11\compose.yaml"
$temp = Join-Path ([IO.Path]::GetTempPath()) ("ksm-backup-export-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
$env:KSM_SECRET_KEY = Get-KsmDemoSecret
try {
    # Export only the dedicated backup volume: never copy the live SQLite file.
    Invoke-KsmCommand "docker" @("compose", "-f", $compose, "--profile", "scheduled-backups",
        "cp", "backup-worker:/app/backups/.", $temp)
    $copies = @(Get-ChildItem -LiteralPath $temp -File -Filter "ksm-daily-*.sqlite3")
    if ($copies.Count -eq 0) { throw "No completed SQLite backups found in the dedicated Docker volume." }
    if (@($copies | Where-Object { $_.Length -le 0 }).Count -ne 0) {
        throw "A backup is empty; refusing to upload."
    }

    # copy never deletes cloud objects; immutable refuses silently overwriting a changed snapshot.
    Invoke-KsmCommand "rclone" @("copy", $temp, $Remote, "--include",
        "ksm-daily-*.sqlite3", "--immutable", "--transfers", "2", "--checkers", "4")
    # One-way check accepts older backup files already present in the cloud.
    Invoke-KsmCommand "rclone" @("check", $temp, $Remote, "--include",
        "ksm-daily-*.sqlite3", "--one-way")

    # Strong end-to-end offsite restore: download a backup and compare full SHA256.
    $latest = $copies | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $retrieved = Join-Path $temp ("restored-" + $latest.Name)
    Invoke-KsmCommand "rclone" @("copyto", ($Remote.TrimEnd("/") + "/" + $latest.Name), $retrieved)
    $a = (Get-FileHash -LiteralPath $latest.FullName -Algorithm SHA256).Hash
    $b = (Get-FileHash -LiteralPath $retrieved -Algorithm SHA256).Hash
    if ($a -ne $b) { throw "Downloaded Drive backup differs from original SHA256." }

    $result = [ordered]@{
        status = "PASS"
        completed_utc = [DateTime]::UtcNow.ToString("o")
        backups_uploaded_or_verified = $copies.Count
        cloud_remote = $Remote
        downloaded_restore_filename = $latest.Name
        sha256_match = $true
        limitations = "Runs only while Windows/Docker/Drive are available. Synthetic demo only."
    }
    $file = Join-Path (Get-KsmDataDir) "last-offsite-check.json"
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $file -Encoding UTF8
    Write-Output ("Verified offsite backup and restore. Evidence: {0}" -f $file)
} finally {
    Remove-Item Env:KSM_SECRET_KEY -ErrorAction SilentlyContinue
    # Never remove the source backup volume. Delete only our temporary copies.
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
