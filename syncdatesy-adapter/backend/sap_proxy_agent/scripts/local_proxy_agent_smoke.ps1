# Local SAP proxy hub smoke test without the full ODATA agent.
# Starts ODATA hub (if needed), mock agent stream, runs TestSapConnection, then stops child processes.
#
# From adapter repo:
#   .\backend\sap_proxy_agent\scripts\local_proxy_agent_smoke.ps1
#
# Options:
#   -SkipHubStart     Hub already listening on :50051
#   -LeaveRunning     Do not stop hub/mock processes this script started
#   -ProxyAgentId     cust_id / agent_id (default C013)

param(
    [switch] $SkipHubStart,
    [switch] $LeaveRunning,
    [string] $ProxyAgentId = "C013"
)

$ErrorActionPreference = "Stop"

function Get-Paths {
    $scriptsRoot = $PSScriptRoot
    $sapAgentRoot = Split-Path -Parent $scriptsRoot
    $backendRoot = Split-Path -Parent $sapAgentRoot
    $migrationRoot = Join-Path $backendRoot "migration-function"
    $adapterRoot = Split-Path -Parent $backendRoot
    $repoRoot = Split-Path -Parent $adapterRoot
    @{
        BackendRoot    = $backendRoot
        MigrationRoot  = $migrationRoot
        RepoRoot       = $repoRoot
        MockScript     = Join-Path $scriptsRoot "local_mock_proxy_agent.py"
        ConnTestScript = Join-Path $scriptsRoot "local_proxy_agent_connection_test.py"
    }
}

function Test-HubListening {
    $lines = netstat -an 2>$null | Select-String ":50051\s"
    return [bool]$lines
}

function Wait-Hub {
    param([int] $Seconds = 30)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HubListening) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

$paths = Get-Paths
$repoRoot = $paths.RepoRoot
$migrationRoot = $paths.MigrationRoot
$backendRoot = $paths.BackendRoot
$hubDir = Join-Path $repoRoot "odata-agent\server"
$odataClientConfig = Join-Path $repoRoot "odata-agent\client\config.json"
$hubMtlsConfig = Join-Path $repoRoot "odata-agent\datasynchor\datasynchor_project\app\grpc_files\config.json"

foreach ($path in @($migrationRoot, $hubDir, $odataClientConfig, $hubMtlsConfig)) {
    if (-not (Test-Path $path)) {
        Write-Error "Missing path: $path"
    }
}

$env:PYTHONPATH = "$backendRoot;$migrationRoot"

$startedHub = $null
$startedMock = $null

try {
    if (-not $SkipHubStart -and -not (Test-HubListening)) {
        Write-Host "[smoke] Starting ODATA hub (server.py) on :50051..."
        $startedHub = Start-Process -FilePath "python" `
            -ArgumentList "server.py" `
            -WorkingDirectory $hubDir `
            -PassThru `
            -WindowStyle Hidden
        if (-not (Wait-Hub)) {
            Write-Error "Hub did not listen on :50051 within 30s"
        }
        Write-Host "[smoke] Hub is listening."
    }
    elseif (Test-HubListening) {
        Write-Host "[smoke] Hub already on :50051 (not starting another)."
    }
    else {
        Write-Error "Port :50051 is not listening. Remove -SkipHubStart or start server.py manually."
    }

    $env:LOCAL_AGENT_CONFIG = $odataClientConfig
    $env:LOCAL_HUB_MTLS_CONFIG = $hubMtlsConfig
    $env:LOCAL_PROXY_AGENT_ID = $ProxyAgentId
    $env:PROXY_AGENT_SERVER_HOST = "localhost"
    $env:PROXY_AGENT_SERVER_PORT = "50051"

    Write-Host "[smoke] Starting mock proxy agent (not ODATA agent.py)..."
    $startedMock = Start-Process -FilePath "python" `
        -ArgumentList $paths.MockScript `
        -WorkingDirectory $migrationRoot `
        -PassThru `
        -WindowStyle Hidden
    Start-Sleep -Seconds 3

    Write-Host "[smoke] Calling TestSapConnection..."
    & python $paths.ConnTestScript
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Error "Connection test failed (exit $exitCode)."
    }
    Write-Host "[smoke] PASS"
    exit 0
}
finally {
    if (-not $LeaveRunning) {
        foreach ($proc in @($startedMock, $startedHub)) {
            if ($proc -and -not $proc.HasExited) {
                Write-Host "[smoke] Stopping PID $($proc.Id)..."
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    else {
        if ($startedHub) { Write-Host "[smoke] Hub PID $($startedHub.Id) left running." }
        if ($startedMock) { Write-Host "[smoke] Mock agent PID $($startedMock.Id) left running." }
    }
}
