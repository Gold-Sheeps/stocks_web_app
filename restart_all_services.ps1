$ErrorActionPreference = "Continue"

function Stop-Port([int]$Port) {
    $lines = netstat -ano | findstr ":$Port" | findstr "LISTENING"
    foreach ($line in $lines) {
        $parts = ($line -split "\s+") | Where-Object { $_ -ne "" }
        if ($parts.Count -ge 5) {
            $procId = $parts[-1]
            if ($procId -match "^[0-9]+$") {
                taskkill /PID $procId /F | Out-Null
            }
        }
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$pyVenv = Join-Path $root ".venv\Scripts\python.exe"
$pySys = "C:\Users\o_van\AppData\Local\Programs\Python\Python312\python.exe"

Write-Host "Stopping ports 3000 / 8000 / 8010 ..."
Stop-Port 3000
Stop-Port 8000
Stop-Port 8010
Start-Sleep -Seconds 2

Write-Host "Starting Frontend (3000) ..."
Start-Process -FilePath powershell.exe -ArgumentList "-NoExit", "-Command", "Set-Location '$frontend'; & '$pySys' -m http.server 3000"

Write-Host "Starting Backend (8000) ..."
Start-Process -FilePath powershell.exe -ArgumentList "-NoExit", "-Command", "Set-Location '$backend'; & '$pyVenv' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

Write-Host "Starting API (8010) ..."
Start-Process -FilePath powershell.exe -ArgumentList "-NoExit", "-Command", "Set-Location '$backend'; & '$pyVenv' -m uvicorn app.main:app --host 0.0.0.0 --port 8010"

Start-Sleep -Seconds 4

Write-Host ""
Write-Host "=== PORT CHECK ==="
netstat -ano | findstr :3000
netstat -ano | findstr :8000
netstat -ano | findstr :8010

Write-Host ""
Write-Host "=== HTTP CHECK ==="
try { Write-Host ("Front 3000: " + (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3000/screener.html" -TimeoutSec 8).StatusCode) } catch { Write-Host ("Front 3000: FAIL - " + $_.Exception.Message) }
try { Write-Host ("Back 8000: " + (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 8).StatusCode) } catch { Write-Host ("Back 8000: FAIL - " + $_.Exception.Message) }
try { Write-Host ("API 8010: " + (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8010/health" -TimeoutSec 8).StatusCode) } catch { Write-Host ("API 8010: FAIL - " + $_.Exception.Message) }

Write-Host ""
Write-Host "Open this URL after success:"
Write-Host "http://127.0.0.1:3000/screener.html"
