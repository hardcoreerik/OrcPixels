# Build the Windows installer: dist\OrcPixels-Setup-<version>.exe
#   powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1 [-Version 0.2.0] [-Python py]
# Uses its own build venv (build\venv); never touches your dev venv or config.
param([string]$Version = "", [string]$Python = "", [switch]$SkipTests)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $Version) { $Version = (Select-String -Path orcpixels\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value }
if (-not $Python) { $Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" } }
$venv = Join-Path $root "build\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
  if ($Python -eq "py") { & py -3 -m venv $venv } else { & $Python -m venv $venv }
}
$py = "$venv\Scripts\python.exe"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -r requirements.txt -r requirements-ai.txt pyinstaller pystray
if ($LASTEXITCODE) { throw "pip install failed" }
if (-not $SkipTests) {
  & $py -m pytest -q -p no:cacheprovider
  if ($LASTEXITCODE) { throw "tests failed" }
}
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyi packaging\orcpixels.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q "/DAppVersion=$Version" "/DSrcDir=$root\dist\OrcPixels" packaging\OrcPixels.iss
if ($LASTEXITCODE) { throw "ISCC failed" }
$exe = Join-Path $root "dist\OrcPixels-Setup-$Version.exe"
"{0}  {1:N1} MB" -f $exe, ((Get-Item $exe).Length / 1MB)
