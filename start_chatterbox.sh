#!/bin/sh
# Start the optional server-side Chatterbox provider.
# Keep this listener on loopback. The Voice Gateway is the only client-facing API.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CHATTERBOX_HOST="${VC_CHATTERBOX_HOST:-127.0.0.1}"
CHATTERBOX_PORT="${VC_CHATTERBOX_PORT:-8765}"
PID_FILE="${VC_CHATTERBOX_PID_FILE:-/tmp/voice-channel-chatterbox.pid}"
LOG_FILE="${VC_CHATTERBOX_LOG_FILE:-/tmp/voice-channel-chatterbox.log}"

if [ -n "${VC_CHATTERBOX_PYTHON:-}" ]; then
  CHATTERBOX_PYTHON="$VC_CHATTERBOX_PYTHON"
else
  CHATTERBOX_PYTHON="$ROOT_DIR/.venv-chatterbox/bin/python"
fi

if [ "${1:-}" = "--status" ]; then
  if curl -fsS --max-time 2 "http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}/" >/dev/null 2>&1; then
    echo "Chatterbox is ready at http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}"
    exit 0
  fi
  echo "Chatterbox is not ready at http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}"
  exit 1
fi

if [ "${1:-}" = "--stop" ]; then
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE")
    case "$pid" in
      ''|*[!0-9]*) ;;
      *) kill "$pid" 2>/dev/null || true ;;
    esac
    rm -f "$PID_FILE"
  fi
  exit 0
fi

if [ "${1:-}" = "--background" ]; then
  if curl -fsS --max-time 2 "http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}/" >/dev/null 2>&1; then
    echo "Chatterbox already running at http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}"
    exit 0
  fi
  if [ ! -x "$CHATTERBOX_PYTHON" ]; then
    "$ROOT_DIR/ensure_chatterbox.sh"
    CHATTERBOX_PYTHON="${VC_CHATTERBOX_PYTHON:-$ROOT_DIR/.venv-chatterbox/bin/python}"
  fi
  nohup "$CHATTERBOX_PYTHON" -m mlx_audio.server \
    --host "$CHATTERBOX_HOST" --port "$CHATTERBOX_PORT" \
    >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  attempt=0
  while [ "$attempt" -lt 60 ]; do
    if curl -fsS --max-time 2 "http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}/" >/dev/null 2>&1; then
      echo "Chatterbox ready at http://${CHATTERBOX_HOST}:${CHATTERBOX_PORT}"
      exit 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "Chatterbox failed to become ready; see $LOG_FILE" >&2
  exit 1
fi

if [ ! -x "$CHATTERBOX_PYTHON" ]; then
  "$ROOT_DIR/ensure_chatterbox.sh"
  CHATTERBOX_PYTHON="${VC_CHATTERBOX_PYTHON:-$ROOT_DIR/.venv-chatterbox/bin/python}"
fi

exec "$CHATTERBOX_PYTHON" -m mlx_audio.server \
  --host "$CHATTERBOX_HOST" \
  --port "$CHATTERBOX_PORT"
