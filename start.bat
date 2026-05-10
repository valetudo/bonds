@echo off
cd /d "%~dp0"
echo Avvio Screener Obbligazionario...

if exist .venv\Scripts\python.exe (
  set PYEXE=.venv\Scripts\python.exe
) else (
  set PYEXE=python
)

start "Bonds Screener Server" %PYEXE% app.py
timeout /t 2 /nobreak > nul
start "" "http://127.0.0.1:5070/"
echo.
echo Server in esecuzione nella finestra "Bonds Screener Server".
echo Chiudi quella finestra per spegnerlo.
