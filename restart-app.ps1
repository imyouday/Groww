param(
    [int]$Port = 8501,
    [int]$WaitSeconds = 20
)

# Restart the local Streamlit app cleanly.
#
# Two processes must die, not one. `streamlit run` under a virtualenv starts a launcher that spawns
# the real server as a child, and killing only the launcher leaves the child holding the port. The
# next launch then binds nothing while the orphan answers, which looks like a broken app.
#
# The file watcher is disabled in .streamlit/config.toml, so a restart is required after every
# source edit. That is deliberate, not an oversight: the watcher crawls ~48,800 files because the
# virtualenv lives inside the repo, and it fights st.cache_resource's key computation.

$ErrorActionPreference = 'Stop'

function Stop-Streamlit {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*streamlit*app.py*' } |
        ForEach-Object {
            Write-Host "  stopping pid $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

$repo = $env:GROWW_REPO
if (-not $repo) { $repo = Split-Path -Parent $MyInvocation.MyCommand.Path }
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "No virtualenv at $python - set `$env:GROWW_REPO to the repo root." -ForegroundColor Red
    exit 1
}

Write-Host "Stopping any running instance..."
Stop-Streamlit
Start-Sleep -Seconds 3

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $Port is still held by another program. Close it, then re-run." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Streamlit in a new window..."
Start-Process `
    -FilePath $python `
    -ArgumentList '-m', 'streamlit', 'run', 'app.py', '--server.port', $Port `
    -WorkingDirectory $repo `
    -WindowStyle Minimized

Write-Host "Waiting for the app to answer..."
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/_stcore/health" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            Write-Host "Up: http://localhost:$Port" -ForegroundColor Green
            exit 0
        }
    } catch {
    }
}

Write-Host "Did not respond within $WaitSeconds s. Open a terminal in $repo and run:" -ForegroundColor Yellow
Write-Host "  .venv\Scripts\python -m streamlit run app.py"
exit 1
