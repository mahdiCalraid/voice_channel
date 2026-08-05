#!/bin/sh
# Start the optional server-side Chatterbox provider.
# Keep this listener on loopback. The Voice Gateway is the only client-facing API.
set -eu

CHATTERBOX_PYTHON="${VC_CHATTERBOX_PYTHON:-python3}"
CHATTERBOX_HOST="${VC_CHATTERBOX_HOST:-127.0.0.1}"
CHATTERBOX_PORT="${VC_CHATTERBOX_PORT:-8765}"

exec "$CHATTERBOX_PYTHON" -m mlx_audio.server \
  --host "$CHATTERBOX_HOST" \
  --port "$CHATTERBOX_PORT"
