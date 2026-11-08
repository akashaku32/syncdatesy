# Proxy agent (SAP) — operations

| Doc | Contents |
|-----|----------|
| **[sap_proxy_agent/README.md](../../../backend/sap_proxy_agent/README.md)** | **Canonical proxy stack** — proto, client, hub, gateway, unary |
| **[cloudbuild/README.md](../../../backend/cloudbuild/README.md)** | **Backend Cloud Build** — batch/qa YAMLs (incl. hub + gateway) |
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | **End-to-end flows** + **§2 pod-by-pod matrix** (django / MF / hub / gateway / agent), mermaid diagrams |
| [UNARY-RPC.md](./UNARY-RPC.md) | **`unary_rpc.py` :50053** — bootstrap token, GCP downscoped token, legacy TableStatus* |
| [GATEWAY.md](./GATEWAY.md) | Architecture, deploy, events, generate config, phase-2 gRPC |
| [DNS-AND-TLS.md](./DNS-AND-TLS.md) | GoDaddy A record, cert-manager, why dedicated host |
| **[README-AGENT.md](./README-AGENT.md)** | **On-prem agent README** — copy into agent repo; full checklist |

**Security (mTLS / HMAC / layers):**  
[monorepo `docs/security/proxy-agent-progress.md`](../../../../docs/security/proxy-agent-progress.md)
