#!/bin/sh
# Provision the local MLX-Audio environment used by Chatterbox.
# The environment and model cache intentionally live outside the repository's
# tracked files; generated audio is returned in memory and is never persisted.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_DIR=${VC_CHATTERBOX_ENV_DIR:-"$ROOT_DIR/.venv-chatterbox"}
BOOTSTRAP_PYTHON=${VC_CHATTERBOX_BOOTSTRAP_PYTHON:-python3}
PACKAGE_VERSION=${VC_CHATTERBOX_VERSION:-0.4.7}

if [ -n "${VC_CHATTERBOX_PYTHON:-}" ]; then
    CHATTERBOX_PYTHON=$VC_CHATTERBOX_PYTHON
else
    CHATTERBOX_PYTHON="$ENV_DIR/bin/python"
fi

if [ ! -x "$CHATTERBOX_PYTHON" ]; then
    if [ -n "${VC_CHATTERBOX_PYTHON:-}" ]; then
        echo "VC_CHATTERBOX_PYTHON is not executable: $CHATTERBOX_PYTHON" >&2
        exit 1
    fi
    echo "Creating Chatterbox environment at $ENV_DIR"
    "$BOOTSTRAP_PYTHON" -m venv "$ENV_DIR"
    CHATTERBOX_PYTHON="$ENV_DIR/bin/python"
fi

if ! "$CHATTERBOX_PYTHON" -c 'import mlx_audio.server' >/dev/null 2>&1; then
    echo "Installing mlx-audio==$PACKAGE_VERSION"
    "$CHATTERBOX_PYTHON" -m pip install --upgrade "mlx-audio==$PACKAGE_VERSION"
fi

# mlx-audio keeps parts of its HTTP server dependency set optional, so install
# the complete server seam explicitly rather than allowing a restart to fail
# after provisioning.
if ! "$CHATTERBOX_PYTHON" -c 'import fastapi, multipart, uvicorn, webrtcvad' >/dev/null 2>&1; then
    echo "Installing the Chatterbox HTTP server dependencies"
    "$CHATTERBOX_PYTHON" -m pip install --upgrade \
        'fastapi>=0.100.0' 'uvicorn[standard]>=0.22.0' webrtcvad-wheels python-multipart
fi

printf '%s\n' "$CHATTERBOX_PYTHON"
