# Avvia l'app Bond Ladder (Streamlit) usando il venv dedicato.
$ErrorActionPreference = "Stop"
$venvPy = "C:\Users\Beppe\venvs\bond_ladder\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "Venv non trovato: $venvPy" -ForegroundColor Yellow
    Write-Host "Crealo e installa le dipendenze (una tantum):" -ForegroundColor Yellow
    Write-Host "  python -m venv C:\Users\Beppe\venvs\bond_ladder"
    Write-Host "  C:\Users\Beppe\venvs\bond_ladder\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

$env:PYTHONIOENCODING = "utf-8"
& $venvPy -m streamlit run "$PSScriptRoot\app.py"
