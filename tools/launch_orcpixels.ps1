# OrcPixels launcher: starts the web server (which auto-connects to configured signs) and opens the UI.
# Paths are relative to this script. Python: $env:ORCPIXELS_PYTHON, else tools\launch.local.ps1
# (gitignored, may set $py), else the repo's .venv (created by install.ps1).
$ErrorActionPreference = "SilentlyContinue"
$root = (Get-Item $PSScriptRoot).Parent.FullName
$py   = Join-Path $root ".venv\Scripts\python.exe"
$local = Join-Path $PSScriptRoot "launch.local.ps1"
if (Test-Path $local) { . $local }
if ($env:ORCPIXELS_PYTHON) { $py = $env:ORCPIXELS_PYTHON }
if (-not (Test-Path $py)) {
  Write-Host "OrcPixels: no Python at $py - run install.ps1 first (or set ORCPIXELS_PYTHON)."
  Start-Sleep -Seconds 5; exit 1
}
$port = if ($env:ORCPIXELS_PORT) { $env:ORCPIXELS_PORT } else { "8765" }
$url  = "http://127.0.0.1:$port"
function Up { try { (Invoke-WebRequest "$url/" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }
if (-not (Up)) {
  Start-Process -FilePath $py -ArgumentList "-m","orcpixels","serve","--port",$port -WorkingDirectory $root -WindowStyle Minimized
  for ($i = 0; $i -lt 60 -and -not (Up); $i++) { Start-Sleep -Milliseconds 500 }
}
try { Invoke-WebRequest "$url/api/connect" -Method Post -UseBasicParsing -TimeoutSec 20 | Out-Null } catch {}
Start-Process $url
