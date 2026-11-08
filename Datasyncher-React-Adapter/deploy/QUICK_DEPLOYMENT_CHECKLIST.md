# Quick Deployment Checklist

## Frontend VM (10.128.0.8) - One-Time Setup

- [ ] Install Docker: `sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin`
- [ ] Start Docker: `sudo systemctl enable docker && sudo systemctl start docker`
- [ ] Create directory: `sudo mkdir -p /opt/datasyncher/frontend && sudo chown -R $USER:$USER /opt/datasyncher/frontend`
- [ ] Copy frontend files to `/opt/datasyncher/frontend/frontend/data-syncher-ui/`
- [ ] Copy `docker-compose.frontend.yml` to `/opt/datasyncher/frontend/docker-compose.yml`
- [ ] Build: `cd /opt/datasyncher/frontend && docker compose build`
- [ ] Start: `docker compose up -d`
- [ ] Verify: `curl http://localhost` (should return HTML)

## Backend VM - Update Deployment

- [ ] Pull latest code: `cd /opt/datasyncher/Datasyncher-React-Adapter && git pull`
- [ ] Verify file exists: `ls -la deploy/traefik/dynamic/frontend.yml`
- [ ] Test frontend VM connectivity: `curl http://10.128.0.8:80`
- [ ] Stop services: `docker compose down`
- [ ] Start services: `docker compose up -d`
- [ ] Check Traefik logs: `docker compose logs traefik | grep -i "file"`
- [ ] Verify no frontend service: `docker compose ps | grep frontend` (should return nothing)

## Verification Tests

- [ ] Frontend loads: `curl -k https://qa.datasyncher.com` (should return React app HTML)
- [ ] Django API works: `curl -k https://qa.datasyncher.com/api/v1/`
- [ ] FastAPI works: `curl -k https://qa.datasyncher.com/fast-api/`
- [ ] Migration API works: `curl -k https://qa.datasyncher.com/api-new/`
- [ ] SSL certificate valid: Check browser shows valid certificate
- [ ] Browser test: Visit `https://qa.datasyncher.com` in browser

## Troubleshooting Commands

```bash
# Frontend VM
docker compose logs frontend
docker compose ps
curl http://localhost

# Backend VM
docker compose logs traefik
docker compose ps
curl http://10.128.0.8:80
docker compose exec traefik cat /etc/traefik/dynamic/frontend.yml
```

