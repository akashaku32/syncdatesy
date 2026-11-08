# Local dev — SAP proxy hub client mTLS

Production uses **`PROXY_AGENT_HUB_*_PEM`** env vars or mounted secrets.

For local migration-function / smoke scripts:

1. Copy `config.json.example` → `config.json` (this folder is gitignored).
2. Optionally place dev certs under `mtls/` (see `mtls/README.md`).
3. Or set `SAP_PROXY_AGENT_GRPC_CONFIG` to an absolute path to your JSON.

Run with **`PYTHONPATH`** including `backend` (and `backend/migration-function` when using MF as cwd):

```powershell
$env:PYTHONPATH = "backend;backend\migration-function"
```
