# Registers a daily task that collects the 3 Variti stores on this machine.
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\install_variti_schedule.ps1
# Remove:    Unregister-ScheduledTask -TaskName MarketParserVariti -Confirm:$false
#
# Runs at 08:45 daily, ONLY while you are logged in (it opens a real Chrome
# window, which needs an interactive desktop). Do the one-time Samokat-address
# setup first by running scripts\collect_variti_local.ps1 by hand.

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "collect_variti_local.ps1"
if (-not (Test-Path $script)) { throw "collect_variti_local.ps1 not found next to this script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 8:45am
# Interactive logon (so the Chrome window has a desktop); start if time was missed.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName "MarketParserVariti" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Daily Samokat/Perekrestok/Onlinetrade collection via real Chrome (home IP)" -Force | Out-Null

Write-Host "OK: task 'MarketParserVariti' runs every day at 08:45 (while you are logged in)."
Write-Host "First, run it once by hand to set the Samokat address / solve any captcha:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\collect_variti_local.ps1"
Write-Host "Run the scheduled task now:  Start-ScheduledTask -TaskName MarketParserVariti"
