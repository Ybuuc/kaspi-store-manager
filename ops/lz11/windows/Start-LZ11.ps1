param(
    [switch]$InstallAutoStart,
    [switch]$ValidateOnly,
    [switch]$QuietStartup
)
. (Join-Path $PSScriptRoot "Common.ps1")

if ($ValidateOnly) {
    Write-Output "PASS: Start-LZ11.ps1 parsed; no Docker commands executed"
    return
}
if ($env:OS -ne "Windows_NT") { throw "Windows 10/11 is required" }

$logFile = Join-Path (Get-KsmDataDir) "start-last.log"
Start-Transcript -Path $logFile -Force | Out-Null
try {
    Write-Host "[1/6] Starting LZ11 demo; diagnostics: $logFile" -ForegroundColor Cyan
    $repo = Get-KsmRepoRoot
    $compose = Join-Path $repo "ops\lz11\compose.yaml"
    if (-not (Test-Path -LiteralPath $compose -PathType Leaf)) {
        throw "compose.yaml missing. Clone the entire repository, not a single CMD file."
    }
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) {
        throw "docker.exe not found on PATH. Install/start Docker Desktop and reopen PowerShell."
    }
    Write-Host "[2/6] Waiting for Docker Desktop engine..." -ForegroundColor Cyan
    $env:KSM_SECRET_KEY = Get-KsmDemoSecret -CreateIfMissing
    # Avoid NativeCommandError/STDERR spam when Docker Desktop's named pipe
    # is not created yet. Launch docker info without a visible console.
    $ready = $false
    $desktop = Join-Path ${env:ProgramFiles} "Docker\Docker\Docker Desktop.exe"
    if ($QuietStartup -and (Test-Path -LiteralPath $desktop) -and
        -not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Write-Host "Docker Desktop is not running; opening it after sign-in."
        try {
            Start-Process -FilePath $desktop -WindowStyle Minimized | Out-Null
        } catch {
            Write-Host ("Docker Desktop launch was unsuccessful: " + $_.Exception.Message)
        }
    }
    for ($n = 0; $n -lt 30; $n++) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $docker.Source
        $psi.Arguments = 'info --format "{{.ServerVersion}}"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $process = [System.Diagnostics.Process]::Start($psi)
        try {
            if ($process.WaitForExit(4500) -and $process.ExitCode -eq 0) {
                $ready = $true
                break
            }
            if (-not $process.HasExited) {
                $process.Kill()
                $process.WaitForExit()
            }
        } finally {
            $process.Dispose()
        }
        if ((($n + 1) % 6) -eq 0) {
            Write-Host ("Docker Engine not ready; retry {0}/30" -f ($n + 1))
        }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) {
        throw "Docker Desktop engine is not ready. Start Docker Desktop, then run START_LZ11.cmd manually."
    }

    Write-Host "[3/6] Checking Compose..." -ForegroundColor Cyan
    Invoke-KsmCommand -Executable "docker" -Arguments @("compose", "-f", $compose, "config", "-q")
    Write-Host "[4/6] Building and starting containers..." -ForegroundColor Cyan
    Invoke-KsmCommand -Executable "docker" -Arguments @("compose", "-f", $compose, "up", "-d", "--build", "app", "uptime-kuma")
    Invoke-KsmCommand -Executable "docker" -Arguments @("compose", "-f", $compose, "exec", "-T", "app",
        "python", "-m", "flask", "--app", "app:create_app", "init-db")
    Invoke-KsmCommand -Executable "docker" -Arguments @("compose", "-f", $compose, "--profile", "scheduled-backups",
        "up", "-d", "--build", "backup-worker")
    Invoke-KsmCommand -Executable "docker" -Arguments @("compose", "-f", $compose, "ps")
    Write-Host "[5/6] Testing local HTTP endpoints..." -ForegroundColor Cyan
    foreach ($url in @("http://127.0.0.1:5000/login", "http://127.0.0.1:3001/")) {
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            try {
                $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4
                if ([int]$response.StatusCode -eq 200) { $ok = $true; break }
            } catch { }
            Start-Sleep -Seconds 2
        }
        if (-not $ok) { throw "HTTP 200 not observed from $url. Inspect docker compose logs and $logFile" }
        Write-Host "PASS HTTP 200: $url" -ForegroundColor Green
    }
    Write-Host "[6/6] Startup confirmed." -ForegroundColor Green
    Write-Output "DEMO RUNNING: http://127.0.0.1:5000/login"
    Write-Output "KUMA: http://127.0.0.1:3001 (configure one-time administrator and monitor)"
    Write-Output "Backup worker: first backup runs on startup, then every 900 seconds while Docker runs"
    if ($InstallAutoStart) {
        & (Join-Path $PSScriptRoot "Install-StartupTask.ps1") -ReplaceExisting
    }
} catch {
    if ($QuietStartup) {
        Write-Host ("BACKGROUND START FAILED: " + $_.Exception.Message) -ForegroundColor Yellow
        Write-Host ("Review: " + $logFile)
        # A hidden task must still report failure to Task Scheduler.
        throw
    }
    Write-Host ("START FAILED: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ("Diagnostic log: " + $logFile) -ForegroundColor Yellow
    throw
} finally {
    Remove-Item Env:KSM_SECRET_KEY -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}
