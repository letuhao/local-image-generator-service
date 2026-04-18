#!/bin/sh
# Entrypoint for image-gen-service.
# Kept small and shell-form because we need to expand env vars into the uvicorn
# argv (exec-form CMD cannot do that). `exec` makes uvicorn PID 1 so SIGTERM
# from Docker reaches it cleanly.

set -e

: "${SHUTDOWN_GRACE_S:=90}"

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --timeout-graceful-shutdown "${SHUTDOWN_GRACE_S}"
