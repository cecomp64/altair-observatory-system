<#
.SYNOPSIS
  Registers altaird as a Task Scheduler task on the processing PC (SPEC §4.1).

.DESCRIPTION
  PixInsight needs an interactive desktop, so altaird runs in the user's session:
  - the trigger is "At log on";
  - the task runs only when the user is logged on;
  - it has no window;
  - on failure it restarts every minute, up to 999 times (the watchdog).
  A locked workstation is fine; logging out stops it. Enable auto-logon on a
  dedicated processing PC.

.EXAMPLE
  .\install-task.ps1 -Exe "C:\Program Files\Altair\altair.exe" -Config "C:\ProgramData\Altair\altair.yaml"
#>
param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [string]$Config = "C:\ProgramData\Altair\altair.yaml",
  [string]$TaskName = "altaird",
  [string]$User = "$env:USERDOMAIN\$env:USERNAME"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) { throw "altair executable not found: $Exe" }
if (-not (Test-Path $Config)) { throw "config not found: $Config" }

$action = New-ScheduledTaskAction -Execute $Exe -Argument "--config `"$Config`" serve --windowless"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
  -Description "Altair processing daemon (collect, back up, process with PixInsight)" -Force | Out-Null
Write-Host "Registered task '$TaskName' for $User. Start it now with: Start-ScheduledTask -TaskName $TaskName"
Write-Host "Then check: & `"$Exe`" --config `"$Config`" doctor"
