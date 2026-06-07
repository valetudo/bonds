@echo off
setlocal enableextensions
chcp 65001 >nul
title Bond Ladder
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
rem Venv dedicato, FUORI dal Drive (evita di sincronizzare migliaia di file).
set "VENV_DIR=%USERPROFILE%\venvs\bond_ladder"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PY%" call :setup

echo ============================================================
echo   Bond Ladder - avvio in corso...
echo   Il browser si aprira' tra qualche secondo.
echo   Per fermare il programma: chiudi questa finestra.
echo ============================================================
echo.
"%VENV_PY%" -m streamlit run "%~dp0app.py"
goto :end

:setup
echo ============================================================
echo   Primo avvio: creo l'ambiente Python (una sola volta) in
echo   %VENV_DIR%
echo ============================================================
py -3 -m venv "%VENV_DIR%"
if errorlevel 1 python -m venv "%VENV_DIR%"
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
goto :eof

:end
pause
