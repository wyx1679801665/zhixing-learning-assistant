@echo off

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\evaluate-cloud.ps1" -PreviousReport "%~dp0runtime-data\model-quality-20261111-011729.json"

pause

