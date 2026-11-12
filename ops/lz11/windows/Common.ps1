# Shared helpers for the synthetic LZ11 Windows demo.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-KsmRepoRoot {
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
}

function Get-KsmDataDir {
    $base = [Environment]::GetFolderPath("LocalApplicationData")
    $dir = Join-Path $base "KaspiStoreManagerLZ11"
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    return $dir
}

function Get-KsmDemoSecret {
    param([switch]$CreateIfMissing)
    $path = Join-Path (Get-KsmDataDir) "secret.dpapi.txt"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if (-not $CreateIfMissing) {
            throw "Start-LZ11.ps1 must be executed once to create the protected demo secret."
        }
        $bytes = New-Object byte[] 48
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $clear = [Convert]::ToBase64String($bytes)
        $secure = ConvertTo-SecureString -String $clear -AsPlainText -Force
        $protected = ConvertFrom-SecureString -SecureString $secure
        [IO.File]::WriteAllText($path, $protected)
    }
    $encrypted = [IO.File]::ReadAllText($path)
    $value = ConvertTo-SecureString -String $encrypted
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Invoke-KsmCommand {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("{0} failed with exit code {1}" -f $Executable, $LASTEXITCODE)
    }
}
