# SAP proxy agent gRPC (canonical layout)

All SAP proxy **gRPC** code lives under this tree. Django app logic stays in `apps/elttool/services/proxy_agent_*.py`.

```text
backend/sap_proxy_agent/
├── proto/service.proto          # Single contract (edit here, then regenerate)
├── generated/                   # service_pb2.py, service_pb2_grpc.py
├── client/                      # Platform → hub mTLS client (migration-function)
│   ├── channel_factory.py
│   ├── hub_target.py
│   └── dataynchor_client.py
├── hub/                         # Pod :50051 — loads + ReportProgress relay
│   ├── main.py
│   ├── grpc_progress.py
│   └── progress_relay.py
├── gateway/                     # Pod :8080 — HTTPS progress (agent-api ingress)
│   └── app.py
├── unary/                       # Pod :50053 — unary_rpc.py (bootstrap, GCP, legacy status)
│   ├── unary_rpc.py
│   └── get_gcp_access_token.py
├── dev/                         # local hub client mTLS (config.json.example)
├── scripts/                     # regenerate.ps1, local smoke/mock scripts
└── tests/                       # hub target, progress relay, gateway
```

| Edit | Path |
|------|------|
| gRPC contract | `proto/service.proto` → `scripts/regenerate.ps1` |
| MF → hub client | `client/` — `from sap_proxy_agent.client.dataynchor_client import ...` |
| Hub pod `:50051` | `hub/` |
| HTTPS gateway `:8080` | `gateway/` |
| Unary pod `:50053` (ODATA `unary_rpc.py`) | `unary/unary_rpc.py`, `unary/get_gcp_access_token.py` |
| Local hub mTLS | `dev/config.json` ← copy [`dev/config.json.example`](dev/config.json.example) |
| Cloud Build (batch hub/gateway) | [`../cloudbuild/batch/`](../cloudbuild/README.md) |
| Local smoke | `scripts/local_proxy_agent_smoke.ps1` |

Migration-function [`app.py`](../migration-function/app.py) puts `backend/` (or `/app` in Docker) on `sys.path` so `import sap_proxy_agent` resolves without a separate shim package.

## Regenerate stubs

```powershell
.\backend\sap_proxy_agent\scripts\regenerate.ps1
```

## Docs

- [ARCHITECTURE.md](../../docs/operations/proxy-agent/ARCHITECTURE.md)
- [HUB.md](../../docs/operations/proxy-agent/HUB.md)
- [UNARY-RPC.md](../../docs/operations/proxy-agent/UNARY-RPC.md)
