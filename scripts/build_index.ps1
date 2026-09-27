# Builds the offline index and asks one question, so a fresh machine can be verified in one step.
# Requires the virtual environment; run `.venv\Scripts\Activate.ps1` first, or replace
# `.venv\Scripts\python.exe` with whatever interpreter holds the dependencies.

param(
    [Parameter(Mandatory = $true)]
    [string]$Question,
    [ValidateSet('auto', 'llm', 'extractive')]
    [string]$Provider = 'extractive',
    [switch]$Refresh,
    [switch]$Rebuild
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "python not found at $python; create the environment with `python -m venv .venv` and install requirements.txt"
}

$buildArgs = @('-m', 'src.pipeline', 'build')
if ($Refresh) { $buildArgs += '--refresh' }
if ($Rebuild) { $buildArgs += '--rebuild' }

Write-Host '== build =='
& $python @buildArgs
if ($LASTEXITCODE -ne 0) { throw "build failed with exit code $LASTEXITCODE" }

Write-Host '== ask =='
& $python -m src.pipeline ask $Question --provider $Provider --debug
if ($LASTEXITCODE -ne 0) { throw "ask failed with exit code $LASTEXITCODE" }
