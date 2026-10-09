# Stops what start_demo.ps1 started: app, API, local model, and the Neo4j container
# (stopped, not removed: the graph stays in its Docker volume).
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-LlmPort {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match "^\s*LLM_BASE_URL\s*=\s*(.*)$") {
                try { return ([Uri]$Matches[1].Trim().Trim('"')).Port } catch { }
            }
        }
    }
    return 8080
}

foreach ($port in 8501, 8000, (Get-LlmPort)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host "stopped process on port $port" -ForegroundColor Cyan
    }
}
# Neo4j needs more than Docker's default 10 s to shut down cleanly; -t 60 avoids a forced kill.
docker compose stop -t 60 *> $null
Write-Host "Neo4j container stopped (data kept)." -ForegroundColor Cyan
Write-Host "Done." -ForegroundColor Green
