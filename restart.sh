#!/bin/bash
# Exit on error
set -e

echo "=============================================="
echo "🔄 Restarting Voice Channel Console Service"
echo "=============================================="

# Keep the Gateway's read-only project mounts synchronized with ACLI's durable
# matter registry.  This makes newly registered channels available to model
# controls without requiring a hand-edited compose override for every channel.
GATEWAY_MATTER_OVERRIDE="$(python3 scripts/sync_gateway_matter_mounts.py)"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.override.yml -f "$GATEWAY_MATTER_OVERRIDE")

# Check if docker is running
if ! docker ps > /dev/null 2>&1; then
  echo "❌ Error: Docker is not running or accessible. Please start Docker first."
  exit 1
fi

# Chatterbox is a host-side MLX service: Docker cannot use the Mac GPU. Keep it
# loopback-only and start it before the Gateway so the first browser load sees
# the preferred narrator instead of caching the browser fallback status.
if [ "${VC_CHATTERBOX_AUTOSTART:-1}" = "1" ]; then
  echo "Starting local Chatterbox narrator..."
  ./start_chatterbox.sh --background
fi

# Stop and remove existing container
echo "Stopping container..."
docker-compose "${COMPOSE_FILES[@]}" down --remove-orphans

# Build and start container in the background
echo "Building and starting container in background..."
docker-compose "${COMPOSE_FILES[@]}" up --build -d

echo ""
echo "✅ Restart complete!"
echo "----------------------------------------------"
echo "🌐 Local console URL: http://localhost:6891"
echo "📁 Workspace path:    /Users/ed/King/clawd_2/voice_channel"
echo "📡 Rocket.Chat room:  #voice_channel"
echo "----------------------------------------------"
echo "To watch logs, run: docker-compose logs -f"
echo "=============================================="
