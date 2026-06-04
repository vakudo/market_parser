# Registers a Windows Scheduled Task that runs the daily update every day at 09:00.
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
# Remove:    Unregister-ScheduledTask -TaskName MarketParserDaily -Confirm:$false

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "daily_update.ps1"
if (-not (Test-Path $script)) { throw "daily_update.ps1 not found next to this script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00am
# Run even if the scheduled time was missed (PC was off/asleep).
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "MarketParserDaily" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Daily baby-food price collection + Google Sheets sync" -Force | Out-Null

Write-Host "OK: task 'MarketParserDaily' runs $script every day at 09:00."
Write-Host "Check it in Task Scheduler, or run now with: Start-ScheduledTask -TaskName MarketParserDaily"
