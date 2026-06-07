@echo off
setlocal enableextensions
chcp 65001 >nul
title Bond Ladder
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "VENV_PY=C:\Users\Beppe\venvs\bond_ladder\Scripts\python.exe"

if not exist "%VENV_PY%" goto :novenv

echo ============================================================
echo   Bond Ladder - avvio in corso...
echo   Il browser si aprira' tra qualche secondo.
echo   Per fermare il programma: chiudi questa finestra.
echo ============================================================
echo.
"%VENV_PY%" -m streamlit run "%~dp0app.py"
goto :end

:novenv
echo.
echo Venv non trovato in:
echo   %VENV_PY%
echo.
echo Crealo e installa le dipendenze ^(una sola volta^), da questa cartella:
echo   python -m venv C:\Users\Beppe\venvs\bond_ladder
echo   "%VENV_PY%" -m pip install -r requirements.txt
echo.

:end
pause
