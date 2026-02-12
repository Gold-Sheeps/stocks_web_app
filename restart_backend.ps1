$port = 8000
$tcp = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue

if ($tcp) {
    $pid_to_kill = $tcp.OwningProcess
    Write-Host "Killing process on port $port (PID: $pid_to_kill)..."
    Stop-Process -Id $pid_to_kill -Force
} else {
    Write-Host "No process found on port $port."
}

Start-Sleep -Seconds 2

Write-Host "Starting Uvicorn..."
Set-Location "backend"
# Start uvicorn in a new window to keep it running
Start-Process "uv" -ArgumentList "run python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000" -WindowStyle Normal
