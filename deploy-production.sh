#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
PRODUCTION_COMPOSE="$REPO_DIR/docker-compose.production.yml"
PROJECT_MOUNTS="$REPO_DIR/docker-compose.override.yml"
GENERATED_MOUNTS="$REPO_DIR/.voice_gateway_matter_mounts.override.yml"
PROJECT_NAME="voice-channel-production"
CONTAINER_NAME="voice-channel-production"
LOCAL_CONTAINER_NAME="voice-channel-console"

cd "$REPO_DIR"

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not available."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: Missing protected environment file: $ENV_FILE"
  exit 1
fi

if ! docker network inspect rc_default >/dev/null 2>&1; then
  echo "ERROR: Rocket.Chat tunnel network rc_default is not available."
  exit 1
fi

LOCAL_ID_BEFORE="$(docker inspect -f '{{.Id}}' "$LOCAL_CONTAINER_NAME" 2>/dev/null || true)"
LOCAL_STARTED_BEFORE="$(docker inspect -f '{{.State.StartedAt}}' "$LOCAL_CONTAINER_NAME" 2>/dev/null || true)"

python3 scripts/sync_gateway_matter_mounts.py >/dev/null

COMPOSE=(
  docker compose
  --project-name "$PROJECT_NAME"
  --env-file "$ENV_FILE"
  -f "$PRODUCTION_COMPOSE"
  -f "$PROJECT_MOUNTS"
  -f "$GENERATED_MOUNTS"
)

echo "Validating the production configuration..."
"${COMPOSE[@]}" config --quiet

echo "Building the isolated production image..."
"${COMPOSE[@]}" build voice-channel

echo "Starting the isolated production container..."
"${COMPOSE[@]}" up -d --no-deps voice-channel

echo "Waiting for the production health check..."
for attempt in $(seq 1 30); do
  HEALTH="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  if [[ "$HEALTH" == "healthy" ]]; then
    break
  fi
  if [[ "$HEALTH" == "unhealthy" ]]; then
    docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
    echo "ERROR: Production Voice Channel became unhealthy."
    exit 1
  fi
  if [[ "$attempt" == "30" ]]; then
    docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
    echo "ERROR: Production Voice Channel did not become healthy in time."
    exit 1
  fi
  sleep 2
done

docker exec "$CONTAINER_NAME" curl --fail --silent http://127.0.0.1:6891/api/status >/dev/null

LOCAL_ID_AFTER="$(docker inspect -f '{{.Id}}' "$LOCAL_CONTAINER_NAME" 2>/dev/null || true)"
LOCAL_STARTED_AFTER="$(docker inspect -f '{{.State.StartedAt}}' "$LOCAL_CONTAINER_NAME" 2>/dev/null || true)"
if [[ -n "$LOCAL_ID_BEFORE" ]] && { [[ "$LOCAL_ID_BEFORE" != "$LOCAL_ID_AFTER" ]] || [[ "$LOCAL_STARTED_BEFORE" != "$LOCAL_STARTED_AFTER" ]]; }; then
  echo "ERROR: The local development Voice Channel changed during deployment."
  exit 1
fi

echo "Production Voice Channel is healthy."
echo "Cloudflare Tunnel origin: http://voice-channel-production:6891"
echo "The local development Voice Channel was not restarted or replaced."
