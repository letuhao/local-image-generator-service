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
  ComfyUI-AudioTools
  ComfyUI-NAG
  comfyui-adaptiveprompts
  ComfyUI-VFI
  comfy_mtb
  ComfyUI_Comfyroll_CustomNodes
  ComfyUI-mxToolkit
  rgthree-comfy
  ComfyUI-LTXVideo
  10S-Comfy-nodes
  ComfyUI-Custom-Scripts
  ComfyMath
  ComfyUI-Detail-Daemon
  Nvidia_RTX_Nodes_ComfyUI
  ComfyUI-DaSiWa-Nodes
  comfyui-WhiteRabbit
  RES4LYF
  comfyui_controlnet_aux
  ComfyUI-segment-anything-2
  ComfyUI_essentials
  ComfyUI_LayerStyle
  ComfyUI_LayerStyle_Advance
  ComfyUI-Impact-Pack
  ComfyUI-Logic
  ComfyUI-RMBG
  ComfyUI-SeedVR2_VideoUpscaler
  comfyui-ollama
  comfyui-openai-api
  ComfyUI-OpenAI
  ComfyUI-Chibi-Nodes
  comfyui-find-perfect-resolution
  ComfyUI_Fill-Nodes
  ComfyUI-PainterI2Vadvanced
  ComfyUI-PainterLongVideo
  was-node-suite-comfyui
  ComfyUI-Easy-Use
)

for name in "${ORDER[@]}"; do
  install_if_present "$name"
done
