#!/usr/bin/env bash
# ComfyUI sidecar entrypoint.
# Launch flags:
#   --listen 0.0.0.0       — bind all interfaces (container isolation handles exposure)
#   --port 8188            — matches docker-compose internal port
#   --preview-method none  — no steady-state preview JPEGs; we don't use them and
#                            they waste CPU + bandwidth
#   --output-directory     — container-local output; adapter streams via /view

set -euo pipefail

# Activate the venv installed during Dockerfile build.
source /workspace/.venv/bin/activate

cd /workspace/ComfyUI

# COMFY_EXTRA_ARGS is a dev escape hatch for e.g. --verbose or --cpu. Unset in prod.
exec python main.py \
    --listen 0.0.0.0 \
    --port 8188 \
    --preview-method none \
    --output-directory /workspace/ComfyUI/output \
    ${COMFY_EXTRA_ARGS:-}
