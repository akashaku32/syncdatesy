#!/usr/bin/env bash
# Create GKE cluster datasyncher-batch-cluster and apply batch-cluster manifests.
# Prereqs: gcloud auth, billing, container.googleapis.com API, helm.
#
# DNS: point gcp.datasyncher.com → ingress-nginx EXTERNAL-IP after install.
#
# Usage (Git Bash / WSL / macOS / Linux):
#   chmod +x k8s/batch-cluster/bootstrap-cluster.sh
#   ./k8s/batch-cluster/bootstrap-cluster.sh
#
# Optional:
#   GCP_PROJECT=datasyncher-kubernetes GCP_REGION=us-central1 GCP_ZONE=us-central1-a \
#   GKE_CLUSTER=datasyncher-batch-cluster ./k8s/batch-cluster/bootstrap-cluster.sh

set -euo pipefail

ADAPTER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ADAPTER_ROOT}"

: "${GCP_PROJECT:=datasyncher-batch}"
: "${GCP_REGION:=us-central1}"
: "${GCP_ZONE:=us-central1-a}"
: "${GKE_CLUSTER:=datasyncher-batch-cluster}"

echo "==> Project=${GCP_PROJECT} Region=${GCP_REGION} Zone=${GCP_ZONE} Cluster=${GKE_CLUSTER}"

echo "==> Creating cluster (default pool: node label pool=app, single zone) ..."
if gcloud container clusters describe "${GKE_CLUSTER}" --region="${GCP_REGION}" --project="${GCP_PROJECT}" &>/dev/null; then
  echo "    Cluster already exists; skipping create."
else
  gcloud container clusters create "${GKE_CLUSTER}" \
    --project="${GCP_PROJECT}" \
    --region="${GCP_REGION}" \
    --node-locations="${GCP_ZONE}" \
    --release-channel=regular \
    --enable-ip-alias \
    --num-nodes=2 \
    --machine-type=e2-standard-2 \
    --enable-autoscaling \
    --min-nodes=2 \
    --max-nodes=8 \
    --node-labels=pool=app \
    --scopes=https://www.googleapis.com/auth/cloud-platform
fi

echo "==> Worker pool (pool=worker)"
if gcloud container node-pools describe worker-pool \
  --cluster="${GKE_CLUSTER}" --region="${GCP_REGION}" --project="${GCP_PROJECT}" &>/dev/null; then
  echo "    worker-pool already exists; skipping."
else
  gcloud container node-pools create worker-pool \
    --cluster="${GKE_CLUSTER}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --machine-type=e2-standard-4 \
    --node-locations="${GCP_ZONE}" \
    --enable-autoscaling \
    --num-nodes=0 \
    --min-nodes=0 \
    --max-nodes=10 \
    --node-labels=pool=worker
fi

gcloud container clusters get-credentials "${GKE_CLUSTER}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}"

echo "==> ingress-nginx (regional static IP — run ensure-static-ingress-ip.sh alone to fix an existing cluster)"
bash "${ADAPTER_ROOT}/k8s/batch-cluster/ensure-static-ingress-ip.sh"

echo "==> cert-manager"
if ! kubectl get namespace cert-manager &>/dev/null; then
  helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
  helm repo update
  helm upgrade --install cert-manager jetstack/cert-manager \
    --namespace cert-manager \
    --create-namespace \
    --set crds.enabled=true \
    --wait --timeout=10m
else
  echo "    cert-manager already present."
fi

echo "==> KEDA (ScaledObject CRDs required by k8s/keda/)"
if ! helm status keda -n keda &>/dev/null; then
  helm repo add kedacore https://kedacore.github.io/charts 2>/dev/null || true
  helm repo update
  helm upgrade --install keda kedacore/keda \
    --namespace keda \
    --create-namespace \
    --wait --timeout=10m
else
  echo "    keda already present."
fi

echo "==> Workloads + batch ingress (run apply-manifests.sh)"
bash "${ADAPTER_ROOT}/k8s/batch-cluster/apply-manifests.sh"

echo ""
echo "Next: DNS A record gcp.datasyncher.com → kubectl get svc -n default ingress-nginx-controller"
echo "      Frontend image: npm run build:batch in frontend/data-syncher-ui, then push to gcr.io/${GCP_PROJECT}/..."
