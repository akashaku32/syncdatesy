# ODATA proxy agent (reference)

Legacy on-premises ODATA agent used as a reference for [`Datasyncher-React-Adapter/backend/sap_proxy_agent`](../Datasyncher-React-Adapter/backend/sap_proxy_agent).

## Local configuration

Never commit real credentials or TLS private keys.

| File | Template |
|------|----------|
| `client/config.json` | Copy from [`client/config.example.json`](client/config.example.json) |
| `datasynchor/datasynchor_project/app/grpc_files/config.json` | Copy from [`config.example.json`](datasynchor/datasynchor_project/app/grpc_files/config.example.json) |

Generate or restore mTLS material under `keys/` and `server/mtls/` from your team CA workflow (not stored in git).

## Smoke test (adapter)

From `Datasyncher-React-Adapter`:

```powershell
.\backend\sap_proxy_agent\scripts\local_proxy_agent_smoke.ps1
```

Requires `client/config.json` and hub `grpc_files/config.json` as above.
