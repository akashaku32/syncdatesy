#!/usr/bin/env bash
# Reserve a regional static external IP and assign it to ingress-nginx's LoadBalancer Service.
# Same region as the GKE cluster (regional LB IPs are regional).
#
# Usage (Git Bash / WSL / macOS / Linux):
#   kubectl config use-context gke_PROJECT_REGION_CLUSTER
#   ./k8s/batch-cluster/ensure-static-ingress-ip.sh
#
# Optional env:
#   GCP_PROJECT=datasyncher-kubernetes
#   GCP_REGION=us-central1
#   STATIC_INGRESS_ADDRESS_NAME=datasyncher-batch-ingress-ip

set -euo pipefail

: "${GCP_PROJECT:=datasyncher-batch}"
: "${GCP_REGION:=us-central1}"
: "${STATIC_INGRESS_ADDRESS_NAME:=datasyncher-batch-ingress-ip}"

echo "==> Regional static IP: ${STATIC_INGRESS_ADDRESS_NAME} (${GCP_REGION})"
if gcloud compute addresses describe "${STATIC_INGRESS_ADDRESS_NAME}" \
  --region="${GCP_REGION}" --project="${GCP_PROJECT}" &>/dev/null; then
  echo "    Already exists."
else
  gcloud compute addresses create "${STATIC_INGRESS_ADDRESS_NAME}" \
    --project="${GCP_PROJECT}" \
    --region="${GCP_REGION}" \
    --description="Reserved LB IP for ingress-nginx (${STATIC_INGRESS_ADDRESS_NAME})"
  echo "    Created."
fi

STATIC_IP="$(gcloud compute addresses describe "${STATIC_INGRESS_ADDRESS_NAME}" \
  --region="${GCP_REGION}" --project="${GCP_PROJECT}" --format='value(address)')"
echo "    Address: ${STATIC_IP}"

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>/dev/null || true
helm repo update

echo "==> Helm: ingress-nginx → loadBalancerIP=${STATIC_IP}"
if helm status ingress-nginx -n default &>/dev/null; then
  helm upgrade ingress-nginx ingress-nginx/ingress-nginx \
    --namespace default \
    --reuse-values \
    --set controller.service.loadBalancerIP="${STATIC_IP}"
else
  helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
    --namespace default \
    --set controller.service.type=LoadBalancer \
    --set controller.service.loadBalancerIP="${STATIC_IP}"
fi

echo ""
echo "Point DNS (e.g. gcp.datasyncher.com) A record at: ${STATIC_IP}"
echo "Wait until: kubectl get svc -n default ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}'"
echo "matches ${STATIC_IP} (often within a few minutes)."
