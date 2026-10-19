# Start the locally installed Qdrant development server. Its binary and data stay under .local/.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$qdrantDir = Join-Path $projectRoot '.local\qdrant'
$binary = Join-Path $qdrantDir 'bin\qdrant.exe'
$runDir = Join-Path $qdrantDir 'run'
$healthUrl = 'http://127.0.0.1:6333/collections'

if (-not (Test-Path -LiteralPath $binary)) {
    throw "Qdrant binary is missing at $binary. See README.md for installation."
}

try {
    $existing = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($existing.status -eq 'ok') {
        Write-Output "Qdrant is already running at $healthUrl"
        exit 0
    }
} catch {
    # No healthy server was reachable; start the local binary below.
}

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
# Qdrant defaults to all interfaces; this development instance should accept local requests only.
$env:QDRANT__SERVICE__HOST = '127.0.0.1'
$process = Start-Process -FilePath $binary -WorkingDirectory $runDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runDir 'stdout.log') `
    -RedirectStandardError (Join-Path $runDir 'stderr.log') -PassThru
$process.Id | Set-Content -Path (Join-Path $runDir 'qdrant.pid')

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($response.status -eq 'ok') {
            Write-Output "Qdrant started (PID $($process.Id)) at http://127.0.0.1:6333"
            exit 0
        }
    } catch {
        if ($process.HasExited) { break }
    }
}

throw "Qdrant did not become healthy. Check $runDir\stderr.log"
