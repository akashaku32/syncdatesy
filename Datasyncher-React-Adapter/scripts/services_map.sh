#!/bin/bash

# Directory to service name mapping
declare -A SERVICE_MAP=(
  ["apps"]="deploy"
  ["datasyncher"]="deploy"
  ["backend/fast-api"]="fastapi"
  ["backend/migration-function"]="migration"
)


# Examples (customize as needed)
SERVICE_MAP["services/django"]="django"
SERVICE_MAP["services/fastapi"]="fastapi"
SERVICE_MAP["services/celery-worker"]="celery"
SERVICE_MAP["services/celery-beat"]="celery-beat"
SERVICE_MAP["common"]="django fastapi celery"
SERVICE_MAP["configs"]="traefik"

# Function to map dir -> services
get_services_for_dir() {
  local dir="$1"
  echo "${SERVICE_MAP[$dir]}"
}
