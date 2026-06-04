# Daily baby-food price update (Windows).
# Collects all auto-runnable stores, writes the monthly XLSX and syncs Google Sheets.
# Schedule it with scripts\install_schedule.ps1 (runs every day at 09:00).

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

# project root = parent of this script's folder
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_" + (Get-Date -Format "yyyy-MM-dd_HHmmss") + ".log")

# prefer the project venv if it exists
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

"=== daily update started $(Get-Date -Format s) ===" | Out-File -FilePath $log -Encoding utf8
& $py -m market_parser.cli run --auto *>> $log
"=== finished $(Get-Date -Format s) (exit $LASTEXITCODE) ===" | Out-File -FilePath $log -Append -Encoding utf8
exit $LASTEXITCODE
