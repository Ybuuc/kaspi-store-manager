param([ValidateSet('start','stop','status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$serverScript = Join-Path $PSScriptRoot 'local_catalog_server.py'
$runtimeDir = Join-Path $projectRoot '.runtime'
$processFile = Join-Path $runtimeDir 'local-catalog.pid'

function Get-CatalogProcess {
    if (Test-Path -LiteralPath $processFile) {
        $processNumber = [int](Get-Content -LiteralPath $processFile -Raw)
        $existingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$processNumber"
        if ($existingProcess -and $existingProcess.CommandLine.Contains($serverScript) -and
            $existingProcess.ExecutablePath -eq $pythonPath) { return $existingProcess }
    }
    return $null
}

$catalogProcess = Get-CatalogProcess
if ($Action -eq 'stop') {
    if ($catalogProcess) { Stop-Process -Id $catalogProcess.ProcessId -ErrorAction Stop }
    if (Test-Path -LiteralPath $processFile) { Remove-Item -LiteralPath $processFile }
    Write-Output 'Local catalogue server stopped.'
    exit
}
if ($Action -eq 'status') {
    if ($catalogProcess) { Write-Output "Running: PID $($catalogProcess.ProcessId)" }
    else { Write-Output 'Local catalogue server is not running.' }
    exit
}
if ($catalogProcess) { Write-Output "Already running: PID $($catalogProcess.ProcessId)"; exit }
if (Get-NetTCPConnection -State Listen -LocalPort 8443 -ErrorAction SilentlyContinue) {
    throw 'Port 8443 is already in use. Inspect the existing service; no duplicate was started.'
}
foreach ($name in @('catalog.xml','ca.pem','server.pem','server-key.pem')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".local-catalog\$name"))) {
        throw "Missing local catalogue or TLS file: $name. Run setup_local_catalog.py and supply a real XML."
    }
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$newProcess = Start-Process -FilePath $pythonPath -ArgumentList @('"' + $serverScript + '"') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'catalog-out.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'catalog-error.log')
$newProcess.Id | Set-Content -LiteralPath $processFile
Start-Sleep -Milliseconds 700
if ($newProcess.HasExited) { throw 'Local catalogue server exited; inspect .runtime/catalog-error.log locally.' }
Write-Output "Started loopback HTTPS catalogue: PID $($newProcess.Id). No Windows autostart was added."
