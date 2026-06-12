# Home collection of the 3 Variti stores (samokat / perekrestok / onlinetrade).
#
# These sit behind ServicePipe, which blocks datacenter IPs and any scraping API
# (Zyte/ZenRows hit its rotated captcha). They only collect reliably from a real
# Chrome on a residential RU IP — i.e. this machine. This script drives that:
# it launches a debug Chrome with a persistent profile, opens the 3 category
# pages, runs the existing CDP collectors, and syncs the result to a SEPARATE
# Google Sheet tab so it never overwrites the 16 stores Railway pushes.
#
# One-time setup: run it once interactively, and in the Chrome window it opens
# set your Samokat delivery address (so products show) and solve any captcha.
# Schedule it with scripts\install_variti_schedule.ps1.

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Isolated DB + its own sheet tab, so the home sync can't clobber Railway's data.
# (GOOGLE_SHEET_NAME has no MARKET_PARSER_ prefix — it is the config's env alias.)
$env:MARKET_PARSER_DB_PATH = Join-Path $root "data\variti.sqlite"
if (-not $env:VARITI_SHEET_NAME) {
    $env:GOOGLE_SHEET_NAME = "Variti_30д"
} else {
    $env:GOOGLE_SHEET_NAME = $env:VARITI_SHEET_NAME
}

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("variti_" + (Get-Date -Format "yyyy-MM-dd_HHmmss") + ".log")

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

function Log($msg) { "$((Get-Date -Format s))  $msg" | Tee-Object -FilePath $log -Append }

Log "=== variti collection started ==="

# --- locate Chrome ---
$chrome = $null
foreach ($c in @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)) { if (Test-Path $c) { $chrome = $c; break } }
if (-not $chrome) { Log "Chrome not found"; exit 1 }

# --- launch debug Chrome if 9222 is not already up ---
$profile = Join-Path $root "data\chrome_variti_profile"
New-Item -ItemType Directory -Force -Path $profile | Out-Null
$startedChrome = $false
$portUp = $false
try { $portUp = (Test-NetConnection -ComputerName 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue).TcpTestSucceeded } catch {}
if (-not $portUp) {
    Log "launching debug Chrome ($chrome)"
    $proc = Start-Process -FilePath $chrome -PassThru -ArgumentList @(
        "--remote-debugging-port=9222",
        "--user-data-dir=`"$profile`"",
        "--no-first-run", "--no-default-browser-check"
    )
    $startedChrome = $true
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 700
        try { if ((Test-NetConnection -ComputerName 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue).TcpTestSucceeded) { break } } catch {}
    }
} else {
    Log "reusing Chrome already on port 9222"
}

try {
    & $py -m market_parser.cli init-db *>> $log
    Log "opening store tabs"
    & $py run_logs\open_variti_tabs.py *>> $log
    foreach ($script in @("cdp_pk_collect.py", "cdp_ot_collect.py", "cdp_collect.py")) {
        Log "running $script"
        & $py "run_logs\$script" *>> $log
    }
    Log "syncing Google Sheets tab '$($env:GOOGLE_SHEET_NAME)'"
    & $py -m market_parser.cli sync-google --days 30 *>> $log
}
finally {
    if ($startedChrome -and $proc -and -not $proc.HasExited) {
        Log "closing debug Chrome"
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    Log "=== finished ==="
}
