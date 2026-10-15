@echo off
setlocal
cd /d "%~dp0"
if not exist "VampiresVSWerewolvesGameServer.exe" (
    echo Server executable missing.
    exit /b 1
)
"VampiresVSWerewolvesGameServer.exe"
