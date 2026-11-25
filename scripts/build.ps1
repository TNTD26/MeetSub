param(
    [switch]$Clean,
    [switch]$NoModels
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing .venv. Run setup.ps1 first."
}
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    Write-Host "Installing build dependencies..."
    $Uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($Uv) {
        & uv pip install --python $Python -r requirements-dev.txt
    } else {
        & $Python -m pip install -r requirements-dev.txt
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }
}

if ($Clean) {
    if (Test-Path (Join-Path $Root "build")) { Remove-Item -Recurse -Force (Join-Path $Root "build") }
    if (Test-Path (Join-Path $Root "dist")) { Remove-Item -Recurse -Force (Join-Path $Root "dist") }
}

Write-Host "Building portable MeetSub..." -ForegroundColor Cyan
& $PyInstaller --noconfirm --clean MeetSub.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$Out = Join-Path $Root "dist\MeetSub"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# Ship only the default, commercially usable model set. The app can download
# other speech models or OPUS later from its model manager.
if (-not $NoModels) {
    $ModelTarget = Join-Path $Out "models"
    New-Item -ItemType Directory -Force -Path $ModelTarget | Out-Null
    foreach ($Name in @("small.en", "qwen2.5-1.5b-ct2-int8", "m2m100-418m", "opus-mt-en-zh")) {
        $Source = Join-Path $Root "models\$Name"
        if (-not (Test-Path $Source)) {
            Write-Warning "Model missing: $Source (run scripts\download_models.py --all)"
            continue
        }
        $Destination = Join-Path $ModelTarget $Name
        Write-Host "Copying model $Name..."
        robocopy $Source $Destination /E /XD .cache /R:1 /W:1 /NFL /NDL /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Could not copy model $Name." }
    }
}

foreach ($File in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
    if (Test-Path $File) {
        Copy-Item $File (Join-Path $Out $File) -Force
    }
}
# Windows PowerShell 5.1 reads this script as ANSI, so a literal Chinese
# filename here can become mojibake. Build the name from code points instead.
$QuickName = [string]::Join("", [char[]](0x5FEB, 0x901F, 0x5F00, 0x59CB)) + ".txt"
$QuickPath = Join-Path $Root $QuickName
if (Test-Path $QuickPath) {
    Copy-Item $QuickPath (Join-Path $Out $QuickName) -Force
}
if (Test-Path "docs") {
    Copy-Item "docs" (Join-Path $Out "docs") -Recurse -Force
}
Copy-Item "run.bat" (Join-Path $Out "run.bat") -Force
$BuildInfo = @(
    "MeetSub 1.1.0",
    "Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Python: $(& $Python --version)",
    "Models: small.en, qwen2.5-1.5b, m2m100-418m, opus-mt-en-zh",
    "",
    "Start: MeetSub.exe",
    "Source: local project files; see README.md"
)
Set-Content -Path (Join-Path $Out "BUILD_INFO.txt") -Value $BuildInfo -Encoding UTF8

$hashTargets = @(
    "MeetSub.exe",
    "models\small.en\model.bin",
    "models\qwen2.5-1.5b-ct2-int8\model.bin",
    "models\m2m100-418m\model.bin",
    "models\opus-mt-en-zh\model.bin"
)
$hashLines = foreach ($target in $hashTargets) {
    $full = Join-Path $Out $target
    if (Test-Path $full) {
        $hash = (Get-FileHash -Algorithm SHA256 $full).Hash.ToLower()
        "$hash  $($target -replace '\\','/')"
    }
}
Set-Content -Path (Join-Path $Out "SHA256SUMS.txt") -Value $hashLines -Encoding ASCII

$SizeMb = [math]::Round((Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "Build complete: $Out" -ForegroundColor Green
Write-Host "Total size: $SizeMb MB"
Write-Host "Run: $Out\MeetSub.exe"
