#!/bin/sh
set -eu

if [ -d /codex-host-auth ]; then
  mkdir -p /root/.codex
  cp -R /codex-host-auth/. /root/.codex/ 2>/dev/null || true
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 6891 --reload
