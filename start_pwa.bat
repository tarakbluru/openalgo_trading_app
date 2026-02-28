@echo off
cd /d "%~dp0"
echo PWA Proxy starting...
echo Backend : http://127.0.0.1:5003  (server.py)
echo PWA     : http://127.0.0.1:5004  ^<-- open this in Chrome/Edge
echo.
echo To install as an app: look for the install icon in the address bar
echo Press Ctrl+C to stop
echo.
python pwa\server.py
pause
