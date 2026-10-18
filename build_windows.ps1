[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

if (-not $IsWindows -and $env:OS -ne "Windows_NT") {
    throw "The Windows package must be built on Windows."
}

$VirtualPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if ($Clean -and (Test-Path -LiteralPath (Join-Path $ProjectRoot ".venv"))) {
    $ResolvedVenv = (Resolve-Path -LiteralPath (Join-Path $ProjectRoot ".venv")).Path
    if (-not $ResolvedVenv.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a virtual environment outside the project."
    }
    Remove-Item -LiteralPath $ResolvedVenv -Recurse -Force
}
if (-not (Test-Path -LiteralPath $VirtualPython)) {
    python -m venv .venv
}

& $VirtualPython -m pip install --upgrade pip
& $VirtualPython -m pip install --requirement requirements-windows.txt

$IconPath = Join-Path $ProjectRoot "assets\RedWhisper.ico"
& $VirtualPython -c "from PIL import Image; image=Image.open(r'assets/RedWhisper-icon.png').convert('RGBA'); image.save(r'assets/RedWhisper.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

if (-not $SkipTests) {
    $TestPatterns = @(
        "test_audio_processing.py",
        "test_audio_devices.py",
        "test_hotkey_config.py",
        "test_mlx_whisper_core.py",
        "test_settings_store.py",
        "test_windows_*.py"
    )
    foreach ($Pattern in $TestPatterns) {
        & $VirtualPython -m unittest discover -s tests -p $Pattern
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed for pattern $Pattern"
        }
    }
}

& $VirtualPython -m PyInstaller --noconfirm --clean RedWhisper.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

if (-not $SkipInstaller) {
    $IsccPath = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

    if (-not $IsccPath) {
        Write-Host "Installing Inno Setup with winget..."
        winget install --id JRSoftware.InnoSetup --exact --silent --accept-package-agreements --accept-source-agreements
        $IsccPath = @(
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    }
    if (-not $IsccPath) {
        throw "Inno Setup 6 was not found. Install it or rebuild with -SkipInstaller."
    }
    & $IsccPath "installer\RedWhisper.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed."
    }
}

Write-Host ""
Write-Host "Windows application: dist\RedWhisper\RedWhisper.exe"
if (-not $SkipInstaller) {
    Write-Host "Windows installer:   dist\RedWhisper-Setup-1.6.2.exe"
}
