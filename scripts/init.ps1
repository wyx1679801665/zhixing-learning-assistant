param([switch]$Demo)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
$env:ZHIXING_DATA = Join-Path $projectRoot 'runtime-data'
$action = 'init'
if ($Demo) { $action = 'demo' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m app.cli $action
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed.' }
