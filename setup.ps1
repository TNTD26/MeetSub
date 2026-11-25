param(
    [switch]$SkipModels,
    [string]$IndexUrl = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "MeetSub setup" -ForegroundColor Cyan

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($Uv) {
        Write-Host "Creating .venv with uv (Python 3.12)..."
        & uv venv --python 3.12 .venv
        if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
        $Python = Join-Path $Root ".venv\Scripts\python.exe"
        Write-Host "Installing dependencies..."
        if ($IndexUrl) {
            & uv pip install --python $Python --default-index $IndexUrl -r requirements.txt
        } else {
            & uv pip install --python $Python -r requirements.txt
        }
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    } else {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            Write-Host "Creating .venv with py -3.12..."
            & py -3.12 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw "Python 3.12 was not found. Install Python 3.12 x64 and retry." }
        } else {
            $Py = Get-Command python -ErrorAction SilentlyContinue
            if (-not $Py) { throw "Python not found. Install Python 3.12 x64 (python.org) and retry." }
            Write-Host "Creating .venv with python..."
            & python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw "Could not create .venv" }
        }
        $Python = Join-Path $Root ".venv\Scripts\python.exe"
        Write-Host "Installing dependencies..."
        if ($IndexUrl) {
            & $Python -m pip install --upgrade pip
            & $Python -m pip install --index-url $IndexUrl -r requirements.txt
        } else {
            & $Python -m pip install --upgrade pip
            & $Python -m pip install -r requirements.txt
        }
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    }
} else {
    Write-Host "Existing .venv found."
}

if (-not $SkipModels) {
    $NeedModels = $false
    if (-not (Test-Path "models\small.en\model.bin")) { $NeedModels = $true }
    if (-not (Test-Path "models\qwen2.5-1.5b-ct2-int8\model.bin")) { $NeedModels = $true }
    if (-not (Test-Path "models\m2m100-418m\model.bin")) { $NeedModels = $true }
    if (-not (Test-Path "models\opus-mt-en-zh\model.bin")) { $NeedModels = $true }
    if ($NeedModels) {
        Write-Host "Downloading offline models (about 2.6 GB)..." -ForegroundColor Yellow
        & $Python scripts\download_models.py --all
        if ($LASTEXITCODE -ne 0) { throw "Model download failed. See README troubleshooting." }
    } else {
        Write-Host "Offline models are present."
    }
}

Write-Host ""
Write-Host "Setup complete. Starting MeetSub..." -ForegroundColor Green
Start-Process -FilePath $Python -ArgumentList "-m", "meetsub" -WorkingDirectory $Root
