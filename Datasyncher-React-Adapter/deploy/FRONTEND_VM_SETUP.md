# Frontend VM Deployment Guide

This guide explains how to deploy the React frontend to a separate GCP VM instance.

## Prerequisites

- GCP VM instance with internal IP: **10.128.0.8**
- VM should be in the same VPC network as the backend VM
- Docker and Docker Compose installed on the frontend VM

## VM Setup Steps

### 1. Install Docker on Frontend VM

SSH into the frontend VM and run:

```bash
# Remove old Docker versions if any
sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

# Install prerequisites
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update package list
sudo apt-get update

# Install Docker Engine and Docker Compose plugin
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start and enable Docker
sudo systemctl enable docker
sudo systemctl start docker

# Add your user to docker group (to run docker without sudo)
sudo usermod -aG docker $USER

# Verify Docker installation
docker --version
docker compose version

# Test Docker (you may need to log out and back in for group changes to take effect)
docker run hello-world
```

**Note:** After adding your user to the docker group, you need to log out and log back in (or run `newgrp docker`) for the group change to take effect. Alternatively, you can use `sudo` with docker commands until you log back in.

#### Alternative: Install Docker from Debian Repositories

If you prefer to use Debian's package manager (though Docker's official repo is recommended):

```bash
# Install docker.io from Debian repos
sudo apt-get update
sudo apt-get install -y docker.io

# Install Docker Compose V2 plugin (if available) or standalone
sudo apt-get install -y docker-compose-plugin || {
  # If plugin not available, install docker-compose standalone
  sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose
}

# Start and enable Docker
sudo systemctl enable docker
sudo systemctl start docker

# Add user to docker group
sudo usermod -aG docker $USER

# Verify installation
docker --version
docker compose version  # or docker-compose --version if using standalone
```

### 2. Create Deployment Directory

```bash
# Create directory structure
sudo mkdir -p /opt/datasyncher/frontend
sudo chown -R $USER:$USER /opt/datasyncher/frontend
cd /opt/datasyncher/frontend
```

### 3. Copy Frontend Files

From your local machine or CI/CD pipeline, copy the frontend directory:

```bash
# Option 1: Using SCP from local machine
scp -r frontend/data-syncher-ui user@10.128.0.8:/opt/datasyncher/frontend/

# Option 2: Using Git (if repository is accessible)
cd /opt/datasyncher/frontend
git clone <repository-url> data-syncher-ui
```

**Note:** The directory structure should be:
```
/opt/datasyncher/frontend/
├── docker-compose.yml
└── data-syncher-ui/    ← React app source code
    ├── Dockerfile
    ├── package.json
    └── ...
```

### 4. Copy Docker Compose File

Copy `docker-compose.frontend.yml` to the frontend VM:

```bash
# From local machine
scp docker-compose.frontend.yml user@10.128.0.8:/opt/datasyncher/frontend/docker-compose.yml
```

Or create it directly on the VM:

```bash
# On frontend VM
cd /opt/datasyncher/frontend
nano docker-compose.yml
# Paste the contents of docker-compose.frontend.yml
```

### 5. Configure Environment Variables (Optional)

If you need to customize environment variables, create a `.env` file:

```bash
cd /opt/datasyncher/frontend/data-syncher-ui
cp env.template .env
# Edit .env if needed
```

### 6. Build and Start Frontend

```bash
cd /opt/datasyncher/frontend

# Build the Docker image
docker compose build

# Start the container
docker compose up -d

# Check logs
docker compose logs -f frontend

# Verify it's running
docker compose ps
curl http://localhost
```

### 7. Verify Internal Connectivity

From the backend VM, test connectivity to frontend:

```bash
# SSH into backend VM first
curl http://10.128.0.8:80
```

You should see the HTML content of the React app.

## Updating Frontend

When you need to update the frontend:

```bash
cd /opt/datasyncher/frontend

# Pull latest changes (if using Git)
git pull

# Rebuild and restart
docker compose build
docker compose up -d

# Or use restart
docker compose restart frontend
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker compose logs frontend

# Check if port 80 is in use
sudo netstat -tulpn | grep :80

# Restart Docker if needed
sudo systemctl restart docker
```

### Can't access from backend VM
```bash
# Verify firewall rules allow internal communication
# Check if frontend container is running
docker compose ps

# Test from backend VM
curl -v http://10.128.0.8:80
```

### Build fails
```bash
# Clear Docker cache and rebuild
docker compose build --no-cache frontend
```

## Health Check

The frontend container includes a health check. Monitor it with:

```bash
docker compose ps
# Check the STATUS column for health status
```

## Security Notes

- Frontend VM should NOT have a public IP (or restrict firewall rules)
- Only port 80 needs to be accessible internally
- All external traffic goes through backend VM's Traefik
- SSL termination happens on backend VM only

