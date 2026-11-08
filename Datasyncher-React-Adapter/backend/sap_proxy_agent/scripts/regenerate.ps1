# Regenerate gRPC stubs into sap_proxy_agent/generated/
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$protoDir = Join-Path (Split-Path -Parent $here) "proto"
$outDir = Join-Path (Split-Path -Parent $here) "generated"
Push-Location $protoDir
try {
    python -m grpc_tools.protoc -I. --python_out=$outDir --grpc_python_out=$outDir service.proto
    $grpcFile = Join-Path $outDir "service_pb2_grpc.py"
    (Get-Content $grpcFile -Raw) -replace '^import service_pb2', 'from sap_proxy_agent.generated import service_pb2' | Set-Content $grpcFile -NoNewline
    Write-Host "Regenerated sap_proxy_agent/generated/service_pb2*.py"
}
finally {
    Pop-Location
}
