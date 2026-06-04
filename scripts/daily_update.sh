#!/usr/bin/env bash
# Daily baby-food price update (Linux / VM).
# Collects all auto-runnable stores, writes the monthly XLSX and syncs Google Sheets.
# Schedule via cron, e.g.:  0 9 * * * /path/to/market_parser/scripts/daily_update.sh
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs
export PYTHONIOENCODING=utf-8

PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"

log="logs/daily_$(date +%Y-%m-%d_%H%M%S).log"
echo "=== daily update started $(date -Is) ===" >"$log"
"$PY" -m market_parser.cli run --auto >>"$log" 2>&1
echo "=== finished $(date -Is) (exit $?) ===" >>"$log"
