# DataSyncher SAP proxy agent — progress reporting

Handoff for the **on-prem agent** team. Copy into the agent repo as `README` or link from your docs.

**Primary:** report progress over **gRPC** (`ReportProgress`) on the **same mTLS channel** as `StartDataLoad` / `TriggerDelta`.  
**Fallback:** if gRPC progress fails (or transport is `https`), **POST** to `progress_api_url` with mTLS + HMAC (platform keeps this path deployed).

**Platform security:** [proxy-agent-progress.md](../../../../docs/security/proxy-agent-progress.md)

---

## Summary checklist

| # | Agent change | Required |
|---|----------------|----------|
| 1 | Parse `config.json` (including progress fields) | Yes |
| 2 | Persist `details_for_sts_update` from load accept | Yes |
| 3 | **Primary:** `ReportProgress` gRPC after table/job milestones | Yes |
| 4 | **Fallback:** HTTPS POST + HMAC when gRPC fails or mode is `https` | Per `progress_transport` |
| 5 | Same event names + correlation IDs on both transports | Yes |
| 6 | Retry + `idempotency_key` | Recommended |
| 7 | Firewall: **gRPC port** (e.g. 50051) always; **443** only if HTTPS fallback used | Yes / conditional |

---

## `progress_transport` (from config)

| Value | Behavior |
|-------|----------|
| `grpc` | gRPC `ReportProgress` only |
| `grpc_with_https_fallback` | **Default** — try gRPC first; on failure use HTTPS + HMAC |
| `https` | HTTPS only (fallback / emergency; no gRPC progress) |

DataSyncher still issues `progress_api_url` and `progress_signing_secret` in config so **HTTPS fallback works** without regenerating config when you enable gRPC on the hub.

---

## Config (`config.json`)

From UI: **Generate proxy agent** (`POST /api/v1/proxy-agent/generate/`).

```json
{
  "agent_id": "agent-…",
  "server_host": "qa.datasyncher.com",
  "server_port": 50051,
  "trusted_ca_cert": "-----BEGIN CERTIFICATE-----…",
  "client_cert": "-----BEGIN CERTIFICATE-----…",
  "client_private_key": "-----BEGIN PRIVATE KEY-----…",
  "bootstrap_token": "…",
  "progress_api_url": "https://agent-api.gcp.datasyncher.com/v1/migration-progress",
  "progress_signing_secret": "<hex>",
  "progress_transport": "grpc_with_https_fallback"
}
```

| Field | Primary (gRPC) | Fallback (HTTPS) |
|-------|----------------|------------------|
| `server_host` / `server_port` | gRPC target | — |
| `client_cert` / `client_private_key` | mTLS (loads + progress) | mTLS on `progress_api_url` |
| `progress_api_url` | Unused unless fallback | POST target |
| `progress_signing_secret` | Hub may relay; **required for HTTPS fallback** | HMAC signing |
| `progress_transport` | Mode selector | Mode selector |

**Never in config:** `INTERNAL_API_KEY`.

---

## Correlation IDs (from gRPC load accept)

On **StartDataLoad** / **TriggerDelta**, persist `details_for_sts_update` for the job and send on **every** progress message (gRPC or HTTPS):

- `migration_session_id`, `job_name`, `connection_id`
- optional: `cmp_id`, `user_id`, `log_id`

---

## Primary: gRPC `ReportProgress`

Proto (hub contract): `backend/sap_proxy_agent/proto/service.proto` in DataSyncher repo.

```protobuf
message ProgressRequest {
  string cust_id = 1;              // agent id / cust_id (hub also checks mTLS CN)
  string event = 2;                // table_started | table_completed | …
  int64 migration_session_id = 3;
  string job_name = 4;
  int64 connection_id = 5;
  string table_name = 6;           // table-scoped events
  google.protobuf.Struct payload = 7;  // rows, error, message, …
  string idempotency_key = 8;
}

message ProgressAck {
  bool accepted = 1;
  string overall_status = 2;
  int32 tables_remaining = 3;
  string message = 4;
}
```

**Agent responsibilities:**

1. Reuse existing **gRPC channel** (same mTLS as loads).
2. Call `ReportProgress` after each milestone (same timing as HTTPS doc below).
3. Map JSON-shaped fields into `ProgressRequest` + `payload` Struct (e.g. `rows`, `error`, `message`).
4. Set `cust_id` to enrolled agent id; hub validates mTLS CN.
5. On `accepted == false` or RPC error → if `grpc_with_https_fallback`, use HTTPS path below.

**Hub responsibilities (outside agent repo):** verify agent mTLS, relay to DataSyncher Django ingest (same DB updates as HTTPS gateway). Agent does **not** send `X-INTERNAL-KEY`.

Regenerate `service_pb2*` when hub + agent ship `ReportProgress` together.

### Legacy ODATA agent RPCs → DataSyncher events

If the agent still uses **`:50053`** / `TableStatusService` style callbacks from the ODATA repo, **do not** point those at Django. Map each call to **`ReportProgress`** (or HTTPS JSON) instead:

| Legacy gRPC (ODATA) | DataSyncher `event` | `payload` hints |
|---------------------|---------------------|-----------------|
| `TableStatusUpdate` (`in_progress`, etc.) | `table_started` / `table_progress` | `staging_records_count`, `rows` |
| `TableStatusUpdate` (completed / failed) | `table_completed` / `table_failed` | `rows`, `error_message` |
| `TableETLStatusUpdate` | fields on `table_completed` | `delta_token`, `initial_completed_flag`, `load_method` |
| `DataTransferLogsUpdate` | `table_completed` | `inserts` / `rows`, `columns` (Django sets timestamps) |
| `MigrationStatusLogUpdate` | `batch_completed` or `activity_log` | `message`, `status`, `log_id` |
| `RecordTableFailureUpdate` | `table_failed` | full SAP error in `error_message` |

Hub deploy: [HUB.md](./HUB.md).

---

## Fallback: HTTPS + HMAC

Use when:

- `progress_transport == "https"`, or
- `progress_transport == "grpc_with_https_fallback"` and gRPC progress failed / unavailable.

### mTLS

POST to `progress_api_url` with the **same client cert/key** as gRPC. Server TLS uses public CAs (Let’s Encrypt). Egress **TCP 443** to agent-api host.

### HMAC

```text
timestamp = str(unix_seconds)
signature = hex( HMAC-SHA256( progress_signing_secret, timestamp + "." + raw_body ) )
```

Headers: `Content-Type: application/json`, `X-Agent-Timestamp`, `X-Agent-Signature`, optional `Idempotency-Key`. Skew ±300s. Sign exact body bytes sent.

Do **not** send `X-Agent-UID` or `X-INTERNAL-KEY`.

---

## Events (both transports)

| `event` | When |
|---------|------|
| `table_started` | Table load begins |
| `table_progress` | Optional mid-run |
| `table_completed` | Table succeeded |
| `table_failed` | Table failed |
| `activity_log` | Extra log line |
| `batch_completed` | Job/batch done |

Required: `event`, `migration_session_id`, `job_name`, `connection_id`; table events need `table_name`.

---

## Recommended client flow

```text
on_table_milestone(...):
  if transport in (grpc, grpc_with_https_fallback):
      try ReportProgress(...)
      if ok: return
  if transport in (https, grpc_with_https_fallback):
      post_progress_https(...)  # mTLS + HMAC
```

Short timeout + retries; do not block SAP extract indefinitely.

---

## HTTPS fallback reference (Python)

```python
import hashlib, hmac, json, time, uuid
import httpx

def post_progress_https(cfg, body: dict) -> None:
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(
        cfg["progress_signing_secret"].encode("utf-8"),
        f"{ts}.".encode("utf-8") + raw,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Timestamp": ts,
        "X-Agent-Signature": sig,
        "Idempotency-Key": body.get("idempotency_key") or str(uuid.uuid4()),
    }
    with httpx.Client(
        cert=(cfg["client_cert_path"], cfg["client_key_path"]),
        timeout=30.0,
        verify=True,
    ) as client:
        client.post(cfg["progress_api_url"], content=raw, headers=headers).raise_for_status()
```

---

## Must not (agent)

| Do not | Why |
|--------|-----|
| Send `X-INTERNAL-KEY` | Server/hub only |
| Log `progress_signing_secret` / private keys | Credential leak |
| Drop HTTPS fallback implementation | Needed when hub/gRPC progress is down |
| Skip correlation IDs | DB / pipeline cannot advance |

---

## Platform docs

| Topic | Doc |
|-------|-----|
| Security layers | [proxy-agent-progress.md](../../../../docs/security/proxy-agent-progress.md) |
| HTTPS gateway (fallback path) | [GATEWAY.md](./GATEWAY.md) |
| gRPC hub pod (`:50051`) | [HUB.md](./HUB.md) |
| DNS for agent-api (HTTPS fallback) | [DNS-AND-TLS.md](./DNS-AND-TLS.md) |

---

## Version

| Item | Value |
|------|--------|
| Primary progress | gRPC `ReportProgress` on `server_host:server_port` |
| Fallback progress | HTTPS `/v1/migration-progress` + mTLS + HMAC |
| Default `progress_transport` | `grpc_with_https_fallback` |
