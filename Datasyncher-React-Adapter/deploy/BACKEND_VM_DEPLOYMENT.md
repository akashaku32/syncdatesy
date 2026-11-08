# Backend VM Deployment - Frontend Separation

This guide explains the changes made to separate the frontend from the backend VM.

## Changes Made

### 1. Removed Frontend Service
- Frontend service has been removed from `docker-compose.yml`
- Frontend now runs on a separate VM at internal IP: **10.128.0.8**

### 2. Updated Traefik Configuration
- Added file provider to Traefik for static frontend routing
- Frontend routing configuration is in `deploy/traefik/dynamic/frontend.yml`
- Traefik now routes frontend requests to `http://10.128.0.8:80`

## Deployment Steps

### 1. Update Backend VM

```bash
# Navigate to project directory
cd /opt/datasyncher/Datasyncher-React-Adapter

# Pull latest changes
git pull origin <branch-name>

# Verify Traefik configuration file exists
ls -la deploy/traefik/dynamic/frontend.yml

# Stop current services
docker compose down

# Rebuild if needed (optional)
docker compose build

# Start services
docker compose up -d

# Verify Traefik is running with new config
docker compose logs traefik | grep -i "file"
```

### 2. Verify Frontend Routing

```bash
# Test connectivity to frontend VM
curl http://10.128.0.8:80

# Check Traefik dashboard (if accessible)
# Visit: https://traefik.qa.datasyncher.com
# Or check logs
docker compose logs traefik
```

### 3. Test All Routes

```bash
# Test frontend (should route to 10.128.0.8)
curl -k https://qa.datasyncher.com

# Test Django API
curl -k https://qa.datasyncher.com/api/v1/

# Test FastAPI
curl -k https://qa.datasyncher.com/fast-api/

# Test Migration API
curl -k https://qa.datasyncher.com/api-new/
```

## Configuration Files

### Traefik Static Configuration
- **Location**: `deploy/traefik/dynamic/frontend.yml`
- **Purpose**: Routes frontend requests to frontend VM
- **Frontend VM IP**: 10.128.0.8:80

### Docker Compose
- **File**: `docker-compose.yml`
- **Changes**: Removed frontend service, added Traefik file provider

## Troubleshooting

### Frontend Not Loading

1. **Check Frontend VM connectivity:**
   ```bash
   curl http://10.128.0.8:80
   ```

2. **Check Traefik logs:**
   ```bash
   docker compose logs traefik | tail -50
   ```

3. **Verify Traefik configuration:**
   ```bash
   docker compose exec traefik cat /etc/traefik/dynamic/frontend.yml
   ```

4. **Check Traefik routing:**
   ```bash
   # Access Traefik dashboard
   # Check HTTP -> Routers -> frontend
   ```

### Traefik File Provider Not Working

1. **Verify volume mount:**
   ```bash
   docker compose exec traefik ls -la /etc/traefik/dynamic/
   ```

2. **Check Traefik startup logs:**
   ```bash
   docker compose logs traefik | grep -i "file"
   ```

3. **Restart Traefik:**
   ```bash
   docker compose restart traefik
   ```

### SSL Certificate Issues

If SSL certificate renewal fails:

```bash
# Check Let's Encrypt storage
ls -la letsencrypt/

# Check Traefik logs for ACME errors
docker compose logs traefik | grep -i acme
```

## Service Status

After deployment, verify all services:

```bash
docker compose ps

# Expected services:
# - traefik (ports 80, 443, 8080)
# - web (Django, port 8000)
# - fast-api (port 9090)
# - migration-function (port 8001)
# - redis (port 6379)
# - celery workers
# - NO frontend service
```

## Network Architecture

```
Internet → Backend VM (Traefik :443) → Frontend VM (10.128.0.8:80)
                                    → Django (:8000)
                                    → FastAPI (:9090)
                                    → Migration (:8001)
```

## Rollback (If Needed)

If you need to temporarily revert:

1. Add frontend service back to `docker-compose.yml`
2. Remove Traefik file provider configuration
3. Restart services: `docker compose down && docker compose up -d`

Note: This is not recommended as frontend VM should be used going forward.

