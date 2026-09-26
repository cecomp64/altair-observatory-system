@echo off
rem NINA end-of-sequence External Script for STANDALONE sites (SPEC §4.2).
rem With a Hub, don't install this: `robs end-of-night` tells the Hub, which tells Altair.
rem
rem Writes <NINA folder>\_altair\session-end-<UTC time>.json, which Altair's
rem collector picks up on its next poll of this rig's share.
rem Usage (NINA Advanced Sequencer, End area, External Script):
rem   C:\Tools\altair-session-end.cmd D:\NINA
setlocal
set "NINA_DIR=%~1"
if "%NINA_DIR%"=="" set "NINA_DIR=D:\NINA"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = Join-Path '%NINA_DIR%' '_altair'; New-Item -ItemType Directory -Force -Path $dir | Out-Null;" ^
  "$now = (Get-Date).ToUniversalTime();" ^
  "$body = @{ host = $env:COMPUTERNAME; at = $now.ToString('yyyy-MM-ddTHH:mm:ssZ') } | ConvertTo-Json -Compress;" ^
  "$tmp = Join-Path $dir ('.session-end-' + $now.ToString('yyyyMMddTHHmmssZ') + '.tmp');" ^
  "Set-Content -Path $tmp -Value $body -Encoding ascii;" ^
  "Move-Item -Force $tmp (Join-Path $dir ('session-end-' + $now.ToString('yyyyMMddTHHmmssZ') + '.json'))"
exit /b %ERRORLEVEL%
