#!/bin/bash
# Exit on error
set -e

echo "=============================================="
echo "🔄 Restarting Voice Channel Console Service"
echo "=============================================="

# Check if docker is running
if ! docker ps > /dev/null 2>&1; then
    echo "❌ Error: Docker is not running or accessible. Please start Docker first."
    exit 1
fi

# Stop and remove existing container
echo "Stopping container..."
docker-compose down --remove-orphans

# Build and start container in the background
echo "Building and starting container in background..."
docker-compose up --build -d

echo ""
echo "✅ Restart complete!"
echo "----------------------------------------------"
echo "🌐 Local console URL: http://localhost:6891"
echo "📁 Workspace path:    /Users/ed/King/clawd_2/voice_channel"
echo "📡 Rocket.Chat room:  #voice_channel"
echo "----------------------------------------------"
echo "To watch logs, run: docker-compose logs -f"
echo "=============================================="
