#!/bin/bash

# Setup script for frontend Docker files
# Run this on the server to create all necessary Docker files for the frontend

echo "Setting up frontend Docker files..."

# Create frontend directories if they don't exist
mkdir -p frontend/data-syncher-ui

# Create Dockerfile for frontend
cat > frontend/data-syncher-ui/Dockerfile << 'EOF'
# Multi-stage build for React application

# Stage 1: Build the application
FROM node:20-alpine AS builder

# Set working directory
WORKDIR /app

# Copy package files
COPY package.json ./
COPY package-lock.json* ./

# Install dependencies
# Use npm install if package-lock.json doesn't exist
RUN if [ -f package-lock.json ]; then \
        npm ci --verbose; \
    else \
        npm install --verbose; \
    fi

# Copy the rest of the application
COPY . .

# Build the application
RUN npm run build

# Stage 2: Serve the application with nginx
FROM nginx:alpine

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built application from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose port 80
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
EOF

# Create nginx.conf for frontend
cat > frontend/data-syncher-ui/nginx.conf << 'EOF'
server {
    listen 80;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;

    # Gzip compression for better performance
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/xml+rss application/json application/x-font-ttf font/opentype image/svg+xml image/x-icon;

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Handle React Router - serve index.html for all non-file routes
    # This is the main purpose of nginx in this container
    # All API routing is handled by Traefik
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
}
EOF

# Create .dockerignore for frontend
cat > frontend/data-syncher-ui/.dockerignore << 'EOF'
# Dependencies
node_modules
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Testing
coverage
*.test.js
*.spec.js
__tests__

# Production build
dist
build

# Development
.env.local
.env.development.local
.env.test.local
.env.production.local
*.log

# Editor directories and files
.vscode
.idea
*.swp
*.swo
*~
.DS_Store

# Git
.git
.gitignore

# Documentation
README.md
*.md

# Docker
Dockerfile*
docker-compose*
.dockerignore

# CI/CD
.github
.gitlab-ci.yml
.travis.yml

# Misc
.eslintcache
.prettierrc
.prettierignore
.husky
EOF

# Create env.template for frontend
cat > frontend/data-syncher-ui/env.template << 'EOF'
# Frontend Environment Variables Template

# API Base URL
VITE_API_BASE_URL=http://localhost:8000

# Fast API URL
VITE_FAST_API_URL=http://localhost:8000/fast-api

# Migration Function API URL
VITE_MIGRATION_API_URL=http://localhost:8000/api-new

# Application Environment (development, staging, production)
VITE_APP_ENV=development

# Feature Flags
VITE_ENABLE_DEBUG=false
VITE_ENABLE_ANALYTICS=false

# WebSocket URL (if needed)
VITE_WS_URL=ws://localhost:8000/ws

# Application Name
VITE_APP_NAME=Data Syncher

# Application Version
VITE_APP_VERSION=1.0.0
EOF

# Create development Dockerfile
cat > frontend/data-syncher-ui/Dockerfile.dev << 'EOF'
# Development Dockerfile for React application with hot-reload

FROM node:20-alpine

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --silent

# Copy the rest of the application
COPY . .

# Expose port 5173 (Vite's default port)
EXPOSE 5173

# Start the development server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
EOF

echo "Frontend Docker files created successfully!"
echo ""
echo "Next steps:"
echo "1. Make sure you have the frontend source code in frontend/data-syncher-ui/"
echo "2. If package-lock.json doesn't exist, run: cd frontend/data-syncher-ui && npm install"
echo "3. Run: docker-compose up --build"

