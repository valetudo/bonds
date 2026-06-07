# Avvia l'app Bond Ladder (Streamlit) usando il venv dedicato, FUORI dal Drive.
$ErrorActionPreference = "Stop"
$venvDir = Join-Path $env:USERPROFILE "venvs\bond_ladder"
$venvPy  = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "Primo avvio: creo l'ambiente Python in $venvDir ..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $venvDir }
    else { & python -m venv $venvDir }
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}

$env:PYTHONIOENCODING = "utf-8"
& $venvPy -m streamlit run (Join-Path $PSScriptRoot "app.py")
