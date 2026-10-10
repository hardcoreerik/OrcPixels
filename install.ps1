<#
.SYNOPSIS
  OrcPixels installer for Windows. Safe to run again (idempotent).
.DESCRIPTION
  * checks for Python 3.11+ (py launcher or python on PATH, or -Python <path>)
  * creates .venv in this folder and installs requirements.txt
  * -WithAI also installs requirements-ai.txt (pywin32 + psutil: AI status / buddy feature)
  * copies example configs that are missing (never overwrites your files)
  * -Shortcut creates a desktop shortcut to the launcher (no autostart is ever added)
  * prints the first-run steps
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -WithAI -Shortcut
#>
param(
  [switch]$WithAI,
  [switch]$Shortcut,
  [string]$Python = "",
  [switch]$Quiet
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
function Say($m) { Write-Host "[orcpixels] $m" }

# ---------------------------------------------------------------- Python 3.11+
function PyVersion([string]$cmd, [string[]]$pre) {
  if ($null -eq $pre) { $pre = @() }
  try {
    $v = & $cmd @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -match '^(\d+)\.(\d+)$') { return [version]$v }
  } catch {}
  return $null
}
$cands = @()
if ($Python) { $cands += ,@($Python, @()) }
else {
  foreach ($v in "3.14", "3.13", "3.12", "3.11") { $cands += ,@("py", @("-$v")) }
  $cands += ,@("python", @()); $cands += ,@("python3", @())
}
$py = $null; $pre = @()
foreach ($c in $cands) {
  $ver = PyVersion $c[0] $c[1]
  if ($ver -and $ver -ge [version]"3.11") { $py = $c[0]; $pre = [string[]]$c[1]; if ($null -eq $pre) { $pre = @() }; break }
}
if (-not $py) {
  Write-Host "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), or pass -Python C:\path\to\python.exe"
  exit 1
}
Say "using Python $(PyVersion $py $pre) ($py $($pre -join ' '))"

# ---------------------------------------------------------------- venv + requirements
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  & $venvPy -c "import sys" 2>$null
  if ($LASTEXITCODE -ne 0) { Say ".venv is broken: recreating"; Remove-Item -Recurse -Force (Join-Path $root ".venv") }
}
if (-not (Test-Path $venvPy)) {
  Say "creating .venv"
  & $py @pre -m venv (Join-Path $root ".venv")
  if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
} else { Say ".venv exists" }
$pipq = @(); if ($Quiet) { $pipq += "-q" }
& $venvPy -m pip install @pipq --disable-pip-version-check --upgrade pip | Out-Null
Say "installing requirements.txt"
& $venvPy -m pip install @pipq --disable-pip-version-check -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }
if ($WithAI) {
  Say "installing requirements-ai.txt (AI status / buddy feature)"
  & $venvPy -m pip install @pipq --disable-pip-version-check -r (Join-Path $root "requirements-ai.txt")
  if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements-ai.txt failed" }
}

# ---------------------------------------------------------------- example configs
$copies = @(
  @("config\settings.example.json", "config\settings.json"),
  @("config\apps.local.example.json", "config\apps.local.json"),
  @(".env.example", ".env")
)
foreach ($c in $copies) {
  $src = Join-Path $root $c[0]; $dst = Join-Path $root $c[1]
  if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst; Say "created $($c[1]) from $($c[0])" }
  elseif (Test-Path $dst) { Say "kept existing $($c[1])" }
}
# config\displays.json is not copied: the example has placeholder addresses. The web UI
# writes it when you add a sign (Scan / add display).

# ---------------------------------------------------------------- desktop shortcut (optional)
if ($Shortcut) {
  $desk = [Environment]::GetFolderPath("Desktop")
  $lnk = Join-Path $desk "OrcPixels.lnk"
  $ws = New-Object -ComObject WScript.Shell
  $s = $ws.CreateShortcut($lnk)
  $s.TargetPath = (Get-Command powershell.exe).Source
  $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\tools\launch_orcpixels.ps1`""
  $s.WorkingDirectory = $root
  $ico = Join-Path $root "assets\orcpixels.ico"
  if (Test-Path $ico) { $s.IconLocation = $ico }
  $s.Description = "OrcPixels LED sign studio"
  $s.Save()
  Say "desktop shortcut: $lnk (no autostart added)"
}

Write-Host ""
Write-Host "OrcPixels is installed. First run:"
Write-Host "  1. Start it:   .\.venv\Scripts\python.exe -m orcpixels serve      (or the desktop shortcut / tools\launch_orcpixels.ps1)"
Write-Host "  2. Open        http://127.0.0.1:8765"
Write-Host "  3. Power on your iPixel sign, then Scan / add display in the status panel and pick it."
Write-Host "  4. Optional: Live tab > feed settings: home latitude/longitude, time zone, stations."
if (-not $WithAI) { Write-Host "  5. Optional AI status feature (Windows): re-run with -WithAI" }
Write-Host "  Tests: .\.venv\Scripts\python.exe -m pytest -q"
