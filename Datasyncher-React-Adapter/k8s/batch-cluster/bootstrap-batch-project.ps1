<#
.SYNOPSIS
  Create GKE cluster + node pools in the datasyncher-batch GCP project.

.DESCRIPTION
  Creates datasyncher-batch-cluster with:
    - default pool  → label pool=app     (django, fastapi, frontend, celery-beat-migration, …)
    - control-pool  → label pool=control (celery-worker-control, celery-worker-control-reconcile)
    - worker-pool   → label pool=worker  (legacy shared celery-worker)
    - worker-standard / worker-premium / worker-enterprise (per-job migration workers)

  Then installs ingress-nginx (static IP), cert-manager, KEDA, and applies batch manifests.

.EXAMPLE
  .\k8s\batch-cluster\bootstrap-batch-project.ps1

.EXAMPLE
  Skip manifest apply (cluster + pools only):
  .\k8s\batch-cluster\bootstrap-batch-project.ps1 -SkipManifests
#>
[CmdletBinding()]
param(
    [string] $Project = "datasyncher-batch",
    [string] $Region = "us-central1",
    [string] $Zone = "us-central1-a",
    [string] $Cluster = "datasyncher-batch-cluster",
    [switch] $SkipManifests
)

$ErrorActionPreference = "Stop"
$AdapterRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$BatchDir = $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Test-NodePool($name) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $out = gcloud container node-pools describe $name `
        --cluster=$Cluster --region=$Region --project=$Project 2>&1
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $prev
    return $ok
}

Write-Step "Enable APIs on project $Project"
gcloud services enable `
    container.googleapis.com `
    compute.googleapis.com `
    iam.googleapis.com `
    cloudresourcemanager.googleapis.com `
    redis.googleapis.com `
    --project=$Project

Write-Step "Cluster $Cluster (project=$Project region=$Region zone=$Zone)"
$clusterExists = $false
try {
    gcloud container clusters describe $Cluster --region=$Region --project=$Project 2>$null | Out-Null
    $clusterExists = $LASTEXITCODE -eq 0
} catch { $clusterExists = $false }

if (-not $clusterExists) {
    gcloud container clusters create $Cluster `
        --project=$Project `
        --region=$Region `
        --node-locations=$Zone `
        --release-channel=regular `
        --enable-ip-alias `
        --num-nodes=2 `
        --machine-type=e2-standard-2 `
        --enable-autoscaling `
        --min-nodes=2 `
        --max-nodes=8 `
        --node-labels=pool=app `
        --scopes=https://www.googleapis.com/auth/cloud-platform
    if ($LASTEXITCODE -ne 0) { throw "Cluster create failed" }
}
else {
    Write-Host "    Cluster already exists; skipping create." -ForegroundColor Yellow
}

function Ensure-Pool {
    param(
        [string] $Name,
        [string] $MachineType,
        [int] $MinNodes,
        [int] $MaxNodes,
        [string] $PoolLabel,
        [string] $Taint = ""
    )
    if (Test-NodePool $Name) {
        Write-Host "    Node pool '$Name' already exists; skipping." -ForegroundColor Yellow
        return
    }
    $args = @(
        "container", "node-pools", "create", $Name,
        "--cluster=$Cluster",
        "--region=$Region",
        "--project=$Project",
        "--machine-type=$MachineType",
        "--node-locations=$Zone",
        "--disk-size=100",
        "--enable-autoscaling",
        "--num-nodes=$MinNodes",
        "--min-nodes=$MinNodes",
        "--max-nodes=$MaxNodes",
        "--node-labels=pool=$PoolLabel"
    )
    if ($Taint) { $args += "--node-taints=$Taint" }
    & gcloud @args
    if ($LASTEXITCODE -ne 0) { throw "Node pool create failed: $Name" }
}

Write-Step "Worker pools"
Ensure-Pool -Name "worker-pool" -MachineType "e2-standard-4" -MinNodes 0 -MaxNodes 10 -PoolLabel "worker"
Ensure-Pool -Name "worker-standard" -MachineType "e2-standard-2" -MinNodes 0 -MaxNodes 20 -PoolLabel "worker-standard"
Ensure-Pool -Name "worker-premium" -MachineType "e2-standard-4" -MinNodes 0 -MaxNodes 30 -PoolLabel "worker-premium" -Taint "pool=worker-premium:NoSchedule"
Ensure-Pool -Name "worker-enterprise" -MachineType "e2-standard-8" -MinNodes 0 -MaxNodes 6 -PoolLabel "worker-enterprise" -Taint "pool=worker-enterprise:NoSchedule"

Write-Step "kubectl credentials"
gcloud container clusters get-credentials $Cluster --region=$Region --project=$Project
$ctx = "gke_${Project}_${Region}_${Cluster}"
Write-Host "    Context: $ctx" -ForegroundColor Green

Write-Step "Control pool (celery-worker-control + reconcile)"
$bash = "C:\Program Files\Git\bin\bash.exe"
if (-not (Test-Path $bash)) { throw "Git Bash required for apply-control-pool.sh" }
$adapterUnix = $AdapterRoot -replace '\\', '/'
& $bash -lc "cd '$adapterUnix' && PROJECT=$Project CLUSTER=$Cluster REGION=$Region ZONE=$Zone bash k8s/node-pools/apply-control-pool.sh"

if ($SkipManifests) {
    Write-Host "`nSkipped manifests (-SkipManifests). Pools:" -ForegroundColor Yellow
    gcloud container node-pools list --cluster=$Cluster --region=$Region --project=$Project
    exit 0
}

Write-Step "ingress-nginx + static IP (Git Bash)"
if (-not (Test-Path $bash)) { throw "Git Bash required for ensure-static-ingress-ip.sh" }
& $bash -lc "cd '$($AdapterRoot -replace '\\','/')' && GCP_PROJECT=$Project GCP_REGION=$Region bash k8s/batch-cluster/ensure-static-ingress-ip.sh"

Write-Step "cert-manager"
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$null = kubectl get namespace cert-manager -o name 2>&1
$certMgrMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = $prevErr
if ($certMgrMissing) {
    helm repo add jetstack https://charts.jetstack.io 2>$null
    helm repo update
    helm upgrade --install cert-manager jetstack/cert-manager `
        --namespace cert-manager --create-namespace `
        --set crds.enabled=true --wait --timeout=10m
}

Write-Step "KEDA"
$ErrorActionPreference = "SilentlyContinue"
$null = helm status keda -n keda 2>&1
$kedaMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = $prevErr
if ($kedaMissing) {
    helm repo add kedacore https://kedacore.github.io/charts 2>$null
    helm repo update
    helm upgrade --install keda kedacore/keda `
        --namespace keda --create-namespace --wait --timeout=10m
}

Write-Step "Memorystore Redis (batch VPC — not QA datasyncher-kubernetes)"
$redisHost = & $bash -lc "cd '$($AdapterRoot -replace '\\','/')' && GCP_PROJECT=$Project bash k8s/batch-cluster/ensure-batch-redis.sh" | Select-Object -Last 1
if (-not $redisHost) { throw "Could not resolve batch Redis host" }
Write-Host "    Redis host: $redisHost" -ForegroundColor Green

Write-Step "GCS bucket + Snowflake storage IAM (batch — not QA meta_data_bucket_ds_kubernetes)"
$gcsBucket = & $bash -lc "cd '$($AdapterRoot -replace '\\','/')' && GCP_PROJECT=$Project bash k8s/batch-cluster/ensure-batch-gcs-bucket.sh" | Select-Object -Last 1
if (-not $gcsBucket) { throw "Could not resolve batch GCS bucket" }
Write-Host "    GCS bucket: $gcsBucket (celery-gcs-uploader + DATASYNCHERBATCH SA IAM applied)" -ForegroundColor Green

Write-Step "Apply batch manifests"
$bashApply = "cd '$($AdapterRoot -replace '\\','/')' && GCP_PROJECT=$Project BATCH_REDIS_IP=$redisHost BATCH_SET_ALL_WORKLOAD_IMAGES=1 BATCH_IMAGE_REGISTRY=gcr.io/$Project BATCH_IMAGE_TAG=batch bash k8s/batch-cluster/apply-manifests.sh"
& $bash -lc $bashApply

Write-Step "Summary"
gcloud container node-pools list --cluster=$Cluster --region=$Region --project=$Project
Write-Host "`nCluster ready in project $Project. kubectl context: $ctx" -ForegroundColor Green
Write-Host "DNS: point gcp.datasyncher.com → kubectl get svc ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}'"
