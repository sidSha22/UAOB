# UAOB live-demo launcher: brings up everything that is not already running,
# warms the caches, and opens the app. Assumes the one-time setup is done
# (run_uaob.bat builds the graph on a fresh machine) and Docker Desktop is open.
#
#   start_demo.bat              everything
#   start_demo.bat -NoLlm       retrieval + app only (answers show evidence, no text)
#   start_demo.bat -NoBrowser   do not open the browser
#   start_demo.bat -NoPause     do not wait for Enter at the end
param([switch]$NoLlm, [switch]$NoBrowser, [switch]$NoPause)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ApiPort = 8000
$UiPort = 8501

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }
function Stop-Here($msg) {
    Write-Host ""
    Say "STOPPED: $msg" "Red"
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}
function Test-Url($url, $timeout = 3) {
    try { return ((Invoke-WebRequest -UseBasicParsing $url -TimeoutSec $timeout).StatusCode -lt 500) } catch { return $false }
}
function Test-Port($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
function Wait-For($what, $seconds, [scriptblock]$ready) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $seconds) {
        if (& $ready) { Say ("  {0} ready ({1:N0}s)" -f $what, $sw.Elapsed.TotalSeconds) "Green"; return $true }
        Start-Sleep 2
    }
    return $false
}
function Get-EnvValue($key) {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return $null }
    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*$key\s*=\s*(.*)$") {
            $v = $Matches[1].Trim()
            if ($v.StartsWith('"') -and $v.EndsWith('"') -and $v.Length -ge 2) {
                # python-dotenv reads "..." values with \\ meaning \
                $v = $v.Substring(1, $v.Length - 2).Replace('\\', '\')
            } elseif ($v.StartsWith("'") -and $v.EndsWith("'") -and $v.Length -ge 2) {
                $v = $v.Substring(1, $v.Length - 2)
            }
            return $v
        }
    }
    return $null
}
function Invoke-Neo4j($cypher) {
    $code = "import sys; sys.path.insert(0, 'graph'); from db import run_query; r = run_query(sys.argv[1]); print(list(r[0].values())[0] if r else '')"
    return (python -c $code $cypher 2>$null)
}

Say "=== UAOB demo launcher ===" "White"

# 1. Docker -------------------------------------------------------------------
Say "[1/5] Docker engine"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Say "  Docker engine is not up yet; waiting up to 3 minutes (open Docker Desktop if you have not)..." "Yellow"
    $ok = Wait-For "Docker engine" 180 { docker info *> $null; $LASTEXITCODE -eq 0 }
    if (-not $ok) { Stop-Here "Docker is not running. Open Docker Desktop, wait for it to finish starting, then run this again." }
} else { Say "  Docker engine ready" "Green" }

# 2. Neo4j ----------------------------------------------------------------------
Say "[2/5] Neo4j database"
docker compose up -d *> $null
$ok = Wait-For "Neo4j" 150 { (Invoke-Neo4j "RETURN 1") -eq "1" }
if (-not $ok) { Stop-Here "Neo4j did not answer. Check:  docker logs uaob-neo4j" }
$wo = Invoke-Neo4j "MATCH (w:WorkOrder) RETURN count(w)"
$unembedded = Invoke-Neo4j "MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c)"
Say "  graph holds $wo work orders"
if ([int]$wo -lt 8076) { Stop-Here "The graph is not loaded ($wo work orders, expected 8076). Run run_uaob.bat once to build it, then try again." }
if ([int]$unembedded -gt 0) { Say "  WARNING: $unembedded chunks have no embedding; dense retrieval will be incomplete. Run: python ingest/embed_chunks.py" "Yellow" }

# 3. Start the local model in the background (it loads while the rest starts) --------
Say "[3/5] Language model (loads in the background)"
$llmBase = Get-EnvValue "LLM_BASE_URL"
$llmModel = Get-EnvValue "LLM_MODEL"
$llmPending = $false
if ($NoLlm) {
    Say "  skipped (-NoLlm): the Ask page will show evidence without written answers" "Yellow"
} elseif (-not $llmBase) {
    Say "  LLM_BASE_URL is not set in .env: no written answers (evidence only)" "Yellow"
} elseif (Test-Url "$llmBase/models") {
    Say "  already running at $llmBase" "Green"
} else {
    $uri = [Uri]$llmBase
    $server = (Get-Command llama-server -ErrorAction SilentlyContinue)
    if ($uri.Host -notin @("localhost", "127.0.0.1")) {
        Say "  $llmBase is not local and not reachable: no written answers" "Yellow"
    } elseif (-not $server) {
        Say "  llama-server is not installed / not on PATH: no written answers (see CONTEXT.md step 7)" "Yellow"
    } elseif (-not $llmModel -or -not (Test-Path -LiteralPath $llmModel)) {
        Say "  LLM_MODEL is not a model file that exists ($llmModel): no written answers" "Yellow"
    } else {
        Say "  starting llama-server on port $($uri.Port)"
        # --reasoning off: the model otherwise spends minutes thinking before it answers.
        # -np 1: one slot, so the context memory is not multiplied.
        $llmArgs = @("-m", "`"$llmModel`"", "--port", "$($uri.Port)", "-c", "6144", "-np", "1", "--reasoning", "off")
        Start-Process -FilePath $server.Source -ArgumentList $llmArgs -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Root "uaob_llm.log") -RedirectStandardError (Join-Path $Root "uaob_llm.err.log") | Out-Null
        $llmPending = $true
    }
}

# 4. API and app ------------------------------------------------------------------------
Say "[4/5] API (port $ApiPort) and app (port $UiPort)"
if (Test-Port $ApiPort) { Say "  API already running" "Green" } else {
    Start-Process python -ArgumentList @("-m", "uvicorn", "api.main:app", "--port", "$ApiPort") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Root "uaob_api.log") -RedirectStandardError (Join-Path $Root "uaob_api.err.log") | Out-Null
    if (-not (Wait-For "API" 90 { Test-Url "http://localhost:$ApiPort/health" })) { Stop-Here "The API did not start. See uaob_api.err.log" }
}
# warm the API's own caches in the background (the app has separate ones, warmed by its first visit)
$warmCmd = "try { Invoke-RestMethod -Method Post -Uri http://localhost:$ApiPort/search -ContentType application/json " +
           "-Body '{`"query`":`"Which components are changed out most often?`",`"mode`":`"hybrid`",`"k`":5}' -TimeoutSec 240 | Out-Null } catch {}"
Start-Process powershell -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-Command", $warmCmd) -WindowStyle Hidden | Out-Null

if (Test-Port $UiPort) { Say "  app already running" "Green" } else {
    Start-Process python -ArgumentList @("-m", "streamlit", "run", "ui/app.py", "--server.port", "$UiPort", "--server.headless", "true") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Root "uaob_ui.log") -RedirectStandardError (Join-Path $Root "uaob_ui.err.log") | Out-Null
    if (-not (Wait-For "app" 90 { Test-Url "http://localhost:$UiPort/_stcore/health" })) { Stop-Here "The app did not start. See uaob_ui.err.log" }
}
# Opening the page makes the app load its embedding model and graph index (about 40 s,
# once). It happens now, while the model finishes loading, so the first question is fast.
if (-not $NoBrowser) { Start-Process "http://localhost:$UiPort" }

# 5. Wait for the model -------------------------------------------------------------------
Say "[5/5] Waiting for the language model"
$llmLive = $false
if ($llmPending) {
    $llmLive = Wait-For "language model" 240 { Test-Url "$llmBase/models" }
    if (-not $llmLive) { Say "  The model did not come up in time (see uaob_llm.err.log). Continuing without written answers." "Yellow" }
} elseif ($llmBase -and -not $NoLlm) {
    $llmLive = Test-Url "$llmBase/models"
}

Write-Host ""
Say "=== READY ===" "Green"
Say "  App   http://localhost:$UiPort   (the page may spend ~40 s loading its indexes the first time)"
Say "  API   http://localhost:$ApiPort/docs"
if ($llmLive) { Say "  Model running: written answers on (about 10-30 s each)" } else { Say "  Model not running: the Ask page shows evidence only" "Yellow" }
Say "  Shut everything down afterwards with stop_demo.bat"
Write-Host ""
if (-not $NoPause) { Read-Host "Press Enter to close this window (everything keeps running)" }
