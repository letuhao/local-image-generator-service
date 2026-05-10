#!/usr/bin/env bash
# Never bind-mount this file from Windows (keep CRLF-safe). It runs the host-editable
# script after stripping CR, or falls back to the image-baked copy.
set -eu
HOST_SCRIPT="${COMFY_RUN_SCRIPT_HOST:-/opt/comfyui-clean/run-comfyui.host.sh}"
BAKED_SCRIPT="${COMFY_RUN_SCRIPT_BAKED:-/opt/comfyui-clean/run-comfyui.baked.sh}"
if [[ -r "$HOST_SCRIPT" ]]; then
  exec bash <(sed 's/\r$//' "$HOST_SCRIPT") "$@"
fi
if [[ -r "$BAKED_SCRIPT" ]]; then
  exec bash <(sed 's/\r$//' "$BAKED_SCRIPT") "$@"
fi
echo "No ComfyUI launcher script found. Expected $HOST_SCRIPT (compose mount) or $BAKED_SCRIPT." >&2
exit 1
