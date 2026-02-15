@echo off
cd /d "%~dp0"
echo Starting Trading App...
echo Open: http://localhost:5003
echo Press Ctrl+C to stop
echo.
python server.py
pause
