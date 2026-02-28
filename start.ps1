Set-Location $PSScriptRoot
Write-Host "Starting Trading App..."
Write-Host "Open: http://localhost:5003"
Write-Host "Press Ctrl+C to stop"
Write-Host ""
& "C:\PythonEnv\gamma_upstox\venv\Scripts\Activate.ps1"
python server.py
