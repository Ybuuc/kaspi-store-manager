param(
    [switch]$InstallAutoStart,
    [switch]$ValidateOnly
)
. (Join-Path $PSScriptRoot "Common.ps1")

if ($ValidateOnly) {
    Write-Output "PASS: Start-LZ11.ps1 parsed; no Docker commands executed"
    return
}
if ($env:OS -ne "Windows_NT") { throw "Windows 10/11 is required" }

$docker = Get-Command docker -ErrorAction Stop
$repo = Get-KsmRepoRoot
$compose = Join-Path $repo "ops\lz11\compose.yaml"
$env:KSM_SECRET_KEY = Get-KsmDemoSecret -CreateIfMissing

try {
    $ready = $false
    for ($n = 0; $n -lt 30; $n++) {
        & $docker.Source info --format "{{.ServerVersion}}" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { throw "Docker Desktop engine is not ready" }

    Invoke-KsmCommand "docker" @("compose", "-f", $compose, "up", "-d", "--build", "app", "uptime-kuma")
    Invoke-KsmCommand "docker" @("compose", "-f", $compose, "exec", "-T", "app",
        "python", "-m", "flask", "--app", "app:create_app", "init-db")
    Invoke-KsmCommand "docker" @("compose", "-f", $compose, "--profile", "scheduled-backups",
        "up", "-d", "--build", "backup-worker")
    Invoke-KsmCommand "docker" @("compose", "-f", $compose, "ps")
    Write-Output "DEMO RUNNING: http://127.0.0.1:5000/login"
    Write-Output "KUMA: http://127.0.0.1:3001 (configure one-time administrator and monitor)"
    Write-Output "Backup worker: first backup runs on startup, then every 900 seconds while Docker runs"
    if ($InstallAutoStart) {
        # Explicit opt-in. Per-user Startup folder; no administrator access requested.
        $startup = [Environment]::GetFolderPath("Startup")
        if (-not (Test-Path $startup)) { throw "Current-user Startup directory not found" }
        $launcher = Join-Path $startup "KaspiStoreManagerLZ11.cmd"
        $script = Join-Path $PSScriptRoot "Start-LZ11.ps1"
        $lines = @(
            "@echo off",
            ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $script)
        )
        [IO.File]::WriteAllLines($launcher, $lines, [Text.Encoding]::ASCII)
        Write-Output ("Auto-start registered for current user: {0}" -f $launcher)
    }
} finally {
    Remove-Item Env:KSM_SECRET_KEY -ErrorAction SilentlyContinue
}
