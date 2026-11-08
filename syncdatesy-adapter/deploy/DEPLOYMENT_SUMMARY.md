# Frontend/Backend VM Separation - Deployment Summary

## Overview

The React frontend has been separated from the Django/FastAPI backend into two separate GCP VMs while maintaining a single Traefik reverse proxy and SSL certificate on the backend VM.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Internet Users                            │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTPS :443
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              Backend VM (qa.datasyncher.com)                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Traefik (Port 80, 443) - SSL Termination            │  │
│  │  - Routes / → Frontend VM (10.128.0.8:80)            │  │
│  │  - Routes /api/* → Django (:8000)                     │  │
│  │  - Routes /fast-api/* → FastAPI (:9090)              │  │
│  │  - Routes /api-new/* → Migration (:8001)             │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Django   │  │ FastAPI  │  │Migration │  │  Redis   │    │
│  │ :8000    │  │ :9090    │  │ :8001    │  │ :6379    │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                              │
│  ┌──────────┐  ┌──────────┐                                │
│  │  Celery  │  │ Celery   │                                │
│  │ Workers  │  │  Beat    │                                │
│  └──────────┘  └──────────┘                                │
└───────────────────────┬─────────────────────────────────────┘
                        │ Internal HTTP :80
                        ▼
┌─────────────────────────────────────────────────────────────┐
│           Frontend VM (Internal IP: 10.128.0.8)              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Nginx + React App (Port 80)                         │  │
│  │  - Serves static React build                         │  │
│  │  - No SSL, no Traefik                                │  │
│  │  - Internal network only                             │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Files Created/Modified

### New Files
1. **`docker-compose.frontend.yml`** - Standalone compose file for frontend VM
2. **`deploy/traefik/dynamic/frontend.yml`** - Traefik static configuration for frontend routing
3. **`deploy/FRONTEND_VM_SETUP.md`** - Frontend VM deployment guide
4. **`deploy/BACKEND_VM_DEPLOYMENT.md`** - Backend VM deployment guide
5. **`deploy/DEPLOYMENT_SUMMARY.md`** - This file

### Modified Files
1. **`docker-compose.yml`** - Removed frontend service, added Traefik file provider

## Deployment Steps

### Step 1: Deploy Frontend to New VM

1. SSH into frontend VM (10.128.0.8)
2. Install Docker (see `deploy/FRONTEND_VM_SETUP.md`)
3. Copy frontend files to `/opt/datasyncher/frontend`
4. Copy `docker-compose.frontend.yml` as `docker-compose.yml`
5. Build and start: `docker compose up -d`
6. Verify: `curl http://localhost`

### Step 2: Update Backend VM

1. SSH into backend VM
2. Pull latest code changes
3. Verify `deploy/traefik/dynamic/frontend.yml` exists
4. Restart services: `docker compose down && docker compose up -d`
5. Verify Traefik logs show file provider loaded
6. Test connectivity: `curl http://10.128.0.8:80`

### Step 3: Verify Deployment

1. Test frontend: `curl -k https://qa.datasyncher.com`
2. Test API endpoints:
   - `curl -k https://qa.datasyncher.com/api/v1/`
   - `curl -k https://qa.datasyncher.com/fast-api/`
   - `curl -k https://qa.datasyncher.com/api-new/`
3. Check SSL certificate is valid
4. Test in browser: Visit `https://qa.datasyncher.com`

## Key Configuration Details

### Frontend VM
- **Internal IP**: 10.128.0.8
- **Port**: 80 (HTTP only, internal)
- **Service**: Nginx serving React static files
- **No SSL**: SSL termination happens on backend VM

### Backend VM
- **Domain**: qa.datasyncher.com
- **Ports**: 80 (HTTP), 443 (HTTPS)
- **Traefik**: Routes frontend to 10.128.0.8:80 via internal network
- **SSL**: Let's Encrypt certificate managed by Traefik

### Traefik Routing Priority
- **Priority 10**: Django routes (`/api`, `/admin`, `/static`, `/media`)
- **Priority 10**: FastAPI routes (`/fast-api`)
- **Priority 10**: Migration routes (`/api-new`)
- **Priority 1**: Frontend routes (`/`) - catches everything else

## Network Requirements

1. Both VMs must be in the same GCP VPC network
2. Default VPC firewall rules allow internal communication
3. Frontend VM should NOT have public IP (or restrict firewall)
4. Backend VM exposes ports 80/443 to internet

## Security Considerations

- ✅ SSL termination on backend VM only
- ✅ Frontend VM not directly accessible from internet
- ✅ Internal communication via private network
- ✅ Single domain with unified SSL certificate
- ✅ All traffic goes through Traefik for security policies

## Troubleshooting

### Frontend Not Loading
1. Check frontend VM: `curl http://10.128.0.8:80`
2. Check Traefik logs: `docker compose logs traefik`
3. Verify Traefik config: `docker compose exec traefik cat /etc/traefik/dynamic/frontend.yml`

### API Calls Failing
1. Frontend uses `window.location.origin` - should work automatically
2. Verify backend services are running: `docker compose ps`
3. Check Traefik routing: Access Traefik dashboard

### SSL Certificate Issues
1. Check Let's Encrypt storage: `ls -la letsencrypt/`
2. Review Traefik logs for ACME errors
3. Verify DNS points to backend VM

## Maintenance

### Updating Frontend
```bash
# On frontend VM
cd /opt/datasyncher/frontend
git pull  # or copy new files
docker compose build
docker compose up -d
```

### Updating Backend
```bash
# On backend VM
cd /opt/datasyncher/syncdatesy-adapter
git pull
docker compose build
docker compose up -d
```

### Monitoring
- Frontend health: `docker compose ps` on frontend VM
- Backend services: `docker compose ps` on backend VM
- Traefik dashboard: `https://traefik.qa.datasyncher.com`

## Benefits of This Architecture

1. **Scalability**: Frontend and backend can scale independently
2. **Security**: Frontend not directly exposed to internet
3. **Performance**: Internal network communication is fast
4. **Maintainability**: Clear separation of concerns
5. **Cost**: Can optimize VM sizes independently
6. **Flexibility**: Easy to add more frontend instances if needed

## Next Steps

1. ✅ Deploy frontend to new VM
2. ✅ Update backend VM configuration
3. ✅ Test all routes and functionality
4. ⬜ Monitor for 24-48 hours
5. ⬜ Update CI/CD pipelines if needed
6. ⬜ Document any environment-specific configurations

