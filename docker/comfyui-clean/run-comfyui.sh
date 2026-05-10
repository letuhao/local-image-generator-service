#!/usr/bin/env bash
# One-shot ComfyUI launch for comfyui-clean (venv under /workspace/ComfyUI).
# Same idea as docker/comfyui/entrypoint.sh — COMFY_EXTRA_ARGS comes from compose.
#
# Usage (inside container):
#   /opt/comfyui-clean/run-comfyui.sh
#   /opt/comfyui-clean/run-comfyui.sh --cpu   # appends extra flags after ours
#
set -euo pipefail

COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
cd "$COMFY_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing venv at $COMFY_ROOT/.venv"
  echo "Create: cd $COMFY_ROOT && python3 -m venv .venv && . .venv/bin/activate && pip install ..."
  exit 1
fi

mkdir -p "${COMFY_OUTPUT_DIR:-$COMFY_ROOT/output}"

# shellcheck disable=SC2086
exec .venv/bin/python main.py \
  --listen "${COMFY_LISTEN_ADDR:-0.0.0.0}" \
  --port "${COMFY_PORT:-8188}" \
  --preview-method "${COMFY_PREVIEW_METHOD:-none}" \
  --output-directory "${COMFY_OUTPUT_DIR:-$COMFY_ROOT/output}" \
  ${COMFY_EXTRA_ARGS:-} \
  "$@"
