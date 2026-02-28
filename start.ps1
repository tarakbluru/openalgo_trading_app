Set-Location $PSScriptRoot
& "C:\PythonEnv\gamma_upstox\venv\Scripts\Activate.ps1"

Write-Host "Starting PWA proxy (background)..."
$pwa = Start-Process -FilePath "python" -ArgumentList "pwa\server.py" -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden

Write-Host "PWA     : http://localhost:5004  (pid $($pwa.Id))"
Write-Host "Backend : http://localhost:5003"
Write-Host "Press Ctrl+C to stop"
Write-Host ""

python server.py

# When server.py exits, also stop the PWA
Stop-Process -Id $pwa.Id -ErrorAction SilentlyContinue
