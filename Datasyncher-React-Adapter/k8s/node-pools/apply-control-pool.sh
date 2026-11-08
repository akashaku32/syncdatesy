#!/usr/bin/env bash
# Create GKE control-pool for celery-worker-control + reconcile (orchestration only).
# Idempotent: skips create if pool already exists.
set -euo pipefail

CLUSTER="${CLUSTER:-datasyncher-cluster}"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-datasyncher-kubernetes}"
POOL_NAME="${POOL_NAME:-control-pool}"
MACHINE="${MACHINE:-e2-standard-2}"
MIN_NODES="${MIN_NODES:-1}"
MAX_NODES="${MAX_NODES:-2}"
ZONE="${ZONE:-}"

echo "==> Cluster: $PROJECT / $CLUSTER / $REGION"
echo "==> Control pool: $POOL_NAME ($MACHINE, label pool=control${ZONE:+, zone=$ZONE})"

if gcloud container node-pools describe "$POOL_NAME" \
  --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" &>/dev/null; then
  echo "    Pool $POOL_NAME already exists; skipping create."
else
  create_args=(
    container node-pools create "$POOL_NAME"
    --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT"
    --machine-type="$MACHINE"
    --disk-size=100
    --enable-autoscaling --min-nodes="$MIN_NODES" --max-nodes="$MAX_NODES"
    --node-labels="pool=control"
  )
  if [[ -n "$ZONE" ]]; then
    create_args+=(--node-locations="$ZONE")
  fi
  gcloud "${create_args[@]}"
fi

echo "==> Wait for at least one control node Ready"
for i in $(seq 1 60); do
  ready=$(kubectl get nodes -l "pool=control" --no-headers 2>/dev/null \
    | awk '$2=="Ready"{c++} END{print c+0}')
  if [[ "${ready:-0}" -ge 1 ]]; then
    echo "    $ready control node(s) Ready"
    break
  fi
  echo "    waiting... ($i/60)"
  sleep 10
done

echo "==> Done. Verify:"
gcloud container node-pools list --cluster="$CLUSTER" --region="$REGION" --project="$PROJECT" \
  --filter="name~control"
kubectl get nodes -l pool=control \
  -o custom-columns=NAME:.metadata.name,MACHINE:.metadata.labels.node\.kubernetes\.io/instance-type,STATUS:.status.conditions[-1].type
