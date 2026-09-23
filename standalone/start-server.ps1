$ErrorActionPreference = "Stop"

$executable = Join-Path $PSScriptRoot "visp-memory.exe"
$port = if ($env:VISP_MEMORY_PORT) { $env:VISP_MEMORY_PORT } else { "8000" }

Write-Host "Starting Visp Memory Server"
Write-Host "Dashboard: http://localhost:$port/dashboard"
Write-Host "API docs:  http://localhost:$port/docs"

& $executable serve --port $port
exit $LASTEXITCODE
