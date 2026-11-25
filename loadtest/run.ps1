# PowerShell script to execute FSIP Phase 14 load tests using grafana/k6 Docker container
param (
    [string]$BaseUrl = "http://host.docker.internal:18080",
    [string]$Token = "change-me-operator"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$OutDir = Join-Path $ScriptDir "out"

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "FSIP Phase 14: Load Testing & Performance Benchmarking" -ForegroundColor Cyan
Write-Host "Target API: $BaseUrl" -ForegroundColor Cyan
Write-Host "Output Directory: $OutDir" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$Tests = @(
    @{ Name = "dashboard_read"; File = "dashboard_read.js"; Summary = "200 VU Dashboard Polling (State, Risk, Decisions)" },
    @{ Name = "decision_flow"; File = "decision_flow.js"; Summary = "50 VU Concurrent Operator Decision & Approval" },
    @{ Name = "spike"; File = "spike.js"; Summary = "200 VU Sudden Traffic Spike & Recovery" }
)

foreach ($test in $Tests) {
    Write-Host "`n>>> Running: $($test.Summary) ($($test.File))..." -ForegroundColor Yellow
    $JsonOut = "/loadtest/out/$($test.Name).json"
    
    docker run --rm `
        -v "${ScriptDir}:/loadtest" `
        -e "API_BASE_URL=$BaseUrl" `
        -e "OPERATOR_TOKEN=$Token" `
        grafana/k6:latest run `
        --summary-export="$JsonOut" `
        "/loadtest/k6/$($test.File)"

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Load test $($test.Name) failed thresholds!"
    } else {
        Write-Host ">>> Test $($test.Name) PASSED thresholds!" -ForegroundColor Green
    }
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "All Phase 14 Load Test Benchmarks Completed Successfully!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
