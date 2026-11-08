#!/usr/bin/env bash
# Upsize app-pool from e2-medium to e2-standard-2 (8 GB RAM per node).
# Creates a new pool, drains old e2-medium nodes, then deletes the old pool.
set -euo pipefail

CLUSTER="${CLUSTER:-datasyncher-cluster}"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-datasyncher-kubernetes}"
OLD_POOL="${OLD_POOL:-app-pool}"
NEW_POOL="${NEW_POOL:-app-pool-std2}"
MACHINE="${MACHINE:-e2-standard-2}"
MIN_NODES="${MIN_NODES:-2}"
MAX_NODES="${MAX_NODES:-6}"

echo "==> Cluster: $PROJECT / $CLUSTER / $REGION"
echo "==> Resize: $OLD_POOL (e2-medium) -> $NEW_POOL ($MACHINE)"

echo "==> Create new pool: $NEW_POOL"
if gcloud container node-pools describe "$NEW_POOL" \
  --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" &>/dev/null; then
  echo "    Pool $NEW_POOL already exists; skipping create."
else
  gcloud container node-pools create "$NEW_POOL" \
    --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" \
    --machine-type="$MACHINE" \
    --disk-size=100 \
    --enable-autoscaling --min-nodes="$MIN_NODES" --max-nodes="$MAX_NODES" \
    --node-labels="pool=app"
fi

echo "==> Wait for new nodes to become Ready"
for i in $(seq 1 60); do
  ready=$(kubectl get nodes -l "pool=app,node.kubernetes.io/instance-type=${MACHINE}" \
    --no-headers 2>/dev/null | awk '$2=="Ready"{c++} END{print c+0}')
  if [[ "${ready:-0}" -ge "$MIN_NODES" ]]; then
    echo "    $ready $MACHINE node(s) Ready"
    break
  fi
  echo "    waiting... ($i/60)"
  sleep 10
done

echo "==> Cordon old e2-medium app nodes"
kubectl get nodes -l "pool=app,node.kubernetes.io/instance-type=e2-medium" -o name 2>/dev/null | while read -r node; do
  echo "    cordon ${node#node/}"
  kubectl cordon "${node#node/}" || true
done

echo "==> Drain old e2-medium app nodes"
kubectl get nodes -l "pool=app,node.kubernetes.io/instance-type=e2-medium" -o name 2>/dev/null | while read -r node; do
  echo "    drain ${node#node/}"
  kubectl drain "${node#node/}" --ignore-daemonsets --delete-emptydir-data --force --grace-period=120 || true
done

echo "==> Delete old pool: $OLD_POOL"
gcloud container node-pools delete "$OLD_POOL" \
  --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" \
  --quiet

echo "==> Done. Verify:"
gcloud container node-pools list --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" \
  --filter="name~app"
kubectl get nodes -l pool=app \
  -o custom-columns=NAME:.metadata.name,MACHINE:.metadata.labels.node\.kubernetes\.io/instance-type,STATUS:.status.conditions[-1].type,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory
kubectl top nodes -l pool=app 2>/dev/null || true
