#!/bin/sh
set -eu

if [ "${VC_UVICORN_RELOAD:-1}" = "1" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port 6891 --reload
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 6891
