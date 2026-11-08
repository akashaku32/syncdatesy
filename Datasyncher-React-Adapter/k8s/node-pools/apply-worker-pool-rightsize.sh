#!/usr/bin/env bash
# Recreate GKE worker node pools at right-sized machine types.
# Prerequisite: celery-worker-control on control-pool; no active celery-worker-job pods.
set -euo pipefail

CLUSTER="${CLUSTER:-datasyncher-cluster}"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-datasyncher-kubernetes}"

echo "==> Cluster: $PROJECT / $CLUSTER / $REGION"

drain_pool_nodes() {
  local label="$1"
  kubectl get nodes -l "pool=${label}" -o name 2>/dev/null | while read -r node; do
  echo "Cordon/drain $node"
  kubectl cordon "${node#node/}" || true
  kubectl drain "${node#node/}" --ignore-daemonsets --delete-emptydir-data --force --grace-period=60 || true
  done
}

recreate_pool() {
  local name="$1"
  local machine="$2"
  local min="$3"
  local max="$4"
  local label="$5"
  local taint="${6:-}"

  echo "==> Delete pool: $name (if exists)"
  gcloud container node-pools delete "$name" \
    --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" \
    --quiet 2>/dev/null || true

  echo "==> Create pool: $name machine=$machine"
  local extra=()
  if [[ -n "$taint" ]]; then
    extra+=(--node-taints="$taint")
  fi
  gcloud container node-pools create "$name" \
    --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" \
    --machine-type="$machine" \
    --disk-size=100 \
    --enable-autoscaling --min-nodes="$min" --max-nodes="$max" \
    --node-labels="pool=${label}" \
    "${extra[@]}"
}

echo "==> Drain worker-standard nodes (old worker-pool)"
drain_pool_nodes "worker-standard"

# max-nodes is per zone on a regional cluster. Targets ~100 accounts:
# standard 100 pods, premium/enterprise 50 each (see CELERY_CLUSTER_MAX_*).
recreate_pool "worker-pool" "e2-standard-2" 0 40 "worker-standard" ""
recreate_pool "worker-premium" "e2-standard-4" 0 20 "worker-premium" "pool=worker-premium:NoSchedule"
recreate_pool "worker-enterprise" "e2-standard-8" 0 20 "worker-enterprise" "pool=worker-enterprise:NoSchedule"

echo "==> Done. Verify:"
gcloud container node-pools list --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT"
kubectl get nodes -l 'pool in (worker-standard,worker-premium,worker-enterprise)' -o custom-columns=NAME:.metadata.name,POOL:.metadata.labels.pool,MACHINE:.metadata.labels.node\.kubernetes\.io/instance-type
