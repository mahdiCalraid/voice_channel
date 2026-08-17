#!/bin/sh
set -eu

# Reload supervision is development-only. In the daily-use container it can leave
# the parent holding port 6891 after a worker reload loses its inherited listener,
# producing a process that looks alive while every HTTP request queues forever.
if [ "${VC_UVICORN_RELOAD:-0}" = "1" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port 6891 --reload
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 6891
