#!/usr/bin/env bash
# Install Python deps for pinned custom nodes under ComfyUI/custom_nodes.
# Order matters: lightweight / shared deps first; ComfyUI-Easy-Use last (heavy overrides).
set -euo pipefail

PY="${1:?python path}"
shift
UV=(uv pip install --python "$PY")

cd /workspace/ComfyUI

install_if_present () {
  local subdir="$1"
  local req="custom_nodes/${subdir}/requirements.txt"
  if [[ -f "$req" ]]; then
    echo "install-custom-node-requirements: ${req}"
    "${UV[@]}" -r "$req"
  else
    echo "install-custom-node-requirements: skip (no requirements.txt): ${subdir}"
  fi
}

ORDER=(
  ComfyUI-GGUF
  ComfyUI-KJNodes
  ComfyUI-VideoHelperSuite
  ComfyUI-WanVideoWrapper
  ComfyUI-MMAudio
  ComfyUI-NAG
  comfyui-adaptiveprompts
  ComfyUI-VFI
  comfy_mtb
  ComfyUI_Comfyroll_CustomNodes
  ComfyUI-mxToolkit
  rgthree-comfy
  ComfyUI-Easy-Use
)

for name in "${ORDER[@]}"; do
  install_if_present "$name"
done
