# UAOB one-shot setup + launch for Windows (PowerShell 5+).
# Follows CONTEXT.md "Startup" steps 1-8 in order; every step is safe to re-run.
# Usage: double-click run_uaob.bat in the repo root, or
#        powershell -ExecutionPolicy Bypass -File scripts\setup_and_run.ps1 [-SkipEval] [-NoLaunch]
param([switch]$SkipEval, [switch]$NoLaunch)

$ErrorActionPreference = "Continue"  # native tools write warnings to stderr; we check exit codes instead
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Log = Join-Path $Root "uaob_setup.log"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line -ForegroundColor Cyan
    Add-Content -Path $Log -Value $line -Encoding UTF8
}
function Fail($msg) {
    Log "FAILED: $msg"
    Write-Host "`nSetup stopped. See uaob_setup.log for details." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}
function Run($what, [scriptblock]$cmd) {
    Log "START $what"
    $out = & $cmd 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $out | ForEach-Object { Write-Host $_; Add-Content -Path $Log -Value "    $_" -Encoding UTF8 }
    if ($code -ne 0 -and $code -ne $null) { Fail "$what (exit code $code)" }
    Log "DONE  $what"
    return $out
}

Set-Content -Path $Log -Value "UAOB setup log $(Get-Date)" -Encoding UTF8

# 1. Tooling -----------------------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Fail "Python not found on PATH. Install Python 3.11+ and re-run." }
Run "python version" { python --version } | Out-Null
$ver = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$ver -lt [version]"3.10") { Fail "Python $ver is too old; need 3.11+." }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker not found. Install Docker Desktop and re-run." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found on PATH." }

# Make sure the Docker engine is running (start Docker Desktop if needed).
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Log "Docker engine not running; starting Docker Desktop..."
    $dd = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) { Start-Process $dd } else { Fail "Docker Desktop is installed but not running. Start it and re-run." }
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep 5; docker info *> $null; if ($LASTEXITCODE -eq 0) { $ok = $true; break } }
    if (-not $ok) { Fail "Docker engine did not start within 5 minutes." }
}
Log "Docker engine is running"

# 2. Corpus ------------------------------------------------------------------
if (-not (Test-Path (Join-Path $Root "maintie\data\gold_release.json"))) {
    Run "clone MaintIE corpus" { git clone --depth 1 https://github.com/nlp-tlp/maintie maintie } | Out-Null
} else { Log "MaintIE corpus already present" }

# 3. Config ------------------------------------------------------------------
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    $pw = -join ((48..57) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    (Get-Content (Join-Path $Root ".env.example")) -replace '^NEO4J_PASSWORD=.*', "NEO4J_PASSWORD=$pw" |
        Set-Content -Path $envFile -Encoding ASCII
    Log "Created .env with a generated Neo4j password"
} else { Log ".env already exists (kept as is)" }

# 4. Python deps ---------------------------------------------------------------
Run "pip install -r graph/requirements.txt" { python -m pip install --disable-pip-version-check -r graph/requirements.txt } | Out-Null

# 5. Neo4j ---------------------------------------------------------------------
Run "docker compose up -d" { docker compose up -d } | Out-Null
Log "Waiting for Neo4j to accept connections..."
$up = $false
for ($i = 0; $i -lt 40; $i++) {
    python -c "import sys; sys.path.insert(0,'graph'); from db import run_query; run_query('RETURN 1')" *> $null
    if ($LASTEXITCODE -eq 0) { $up = $true; break }
    Start-Sleep 5
}
if (-not $up) { Fail "Neo4j did not become reachable. Check 'docker logs uaob-neo4j'." }
Run "smoke test (expect 13/13)" { python graph/smoke_test.py } | Out-Null

# 6. Build the graph (skips work already done) ---------------------------------
$count = python -c "import sys; sys.path.insert(0,'graph'); from db import run_query; print(run_query('MATCH (w:WorkOrder) RETURN count(w) AS n')[0]['n'])"
if ([int]$count -lt 8076) {
    if ([int]$count -gt 0) { Fail "Graph is partially loaded ($count work orders). Clear it with 'docker compose down -v' and re-run." }
    Run "load MaintIE into Neo4j" { python ingest/load_maintie.py } | Out-Null
} else { Log "MaintIE already loaded ($count work orders)" }
Run "chunk work orders" { python ingest/chunk_work_orders.py } | Out-Null
Run "chunk manuals" { python ingest/chunk_manuals.py } | Out-Null
Run "embed chunks (first run downloads bge-small)" { python ingest/embed_chunks.py } | Out-Null
Run "node counts" { python -c "import sys; sys.path.insert(0,'graph'); from db import run_query; [print(r['label'], r['n']) for r in run_query('MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC')]" } | Out-Null

# 8. Eval + launch ------------------------------------------------------------
if (-not $SkipEval) { Run "evaluation harness (all four retrievers)" { python eval/harness.py } | Out-Null }

if (-not $NoLaunch) {
    Log "Starting the API on http://localhost:8000 and the app on http://localhost:8501"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; python -m uvicorn api.main:app --port 8000" -WindowStyle Minimized
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; python -m streamlit run ui/app.py --server.port 8501"
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep 2
        try { Invoke-WebRequest -UseBasicParsing http://localhost:8501/_stcore/health -TimeoutSec 2 | Out-Null; $ready = $true; break } catch {}
    }
    if ($ready) { Log "App is up"; Start-Process "http://localhost:8501" } else { Log "App did not answer yet; open http://localhost:8501 in a minute" }
}
Log "ALL DONE"
Write-Host "`nUAOB is running. Keep the two PowerShell windows open while you use it." -ForegroundColor Green
Read-Host "Press Enter to close this window"
