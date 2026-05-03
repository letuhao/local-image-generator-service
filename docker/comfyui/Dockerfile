# ComfyUI sidecar for image-gen-service.
#
# Pin sources (must stay in sync — custom-nodes.txt is canonical):
#   docker/comfyui/custom-nodes.txt     ← human-facing doc of pins
#   docker/comfyui/Dockerfile ARG ...   ← default if compose doesn't override
#   docker-compose.yml  build.args      ← actual build-time pins
# When you bump custom-nodes.txt, bump the other two in the same commit.
#
# Base image: nvidia/cuda runtime (not devel) — we install Python + torch wheels,
# not nvcc-compiled extensions. Size target grows with WAN/video custom nodes.

ARG COMFYUI_REF=v0.20.1
ARG COMFYUI_GGUF_REF=6ea2651e7df66d7585f6ffee804b20e92fb38b8a
# WAN 2.2 Smooth Workflow v5.0 — pins duplicated in docker/comfyui/custom-nodes.txt
ARG COMFYUI_WAN_VIDEO_REF=90c3bbb6c2e4ff5e05305e765d007d5e58428ce4
ARG COMFYUI_MMAUDIO_REF=841b307095f3cdc448d940a5787bc36706e49f1d
ARG COMFYUI_VHS_REF=8923bd836bdab8b7bbdf4ed104b7d045e70c66e2
ARG COMFYUI_KJ_REF=a6b867b63a29ca48ddb15c589e17a9f2d8530d57
ARG RGTHREE_REF=84a146fee39f7b3a8c6631dcac29bc13b077eb49
ARG COMFYUI_EASY_USE_REF=de92038f88317699f314be85e5c7af84f1ab9c3a
ARG COMFYUI_NAG_REF=c6f27116a8259f5b501d498a09e51c82fa72e35f
ARG COMFYUI_ADAPTIVE_PROMPTS_REF=aa0d896b5e5d9c7031c3aa995e028f2507dfc63c
ARG COMFYUI_VFI_REF=6176a430f12cd16003f4664c1e3c6af8e96cc3c6
ARG COMFY_MTB_REF=d87e52ea2c112fd95f257dcd6a54a5db77a34fc3
ARG COMFYUI_COMFYROLL_REF=d78b780ae43fcf8c6b7c6505e6ffb4584281ceca
ARG COMFYUI_MXTOOLKIT_REF=7f7a0e584f12078a1c589645d866ae96bad0cc35

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0

# Python 3.11 + git + curl (healthcheck) + libgl1 (cv2) + ffmpeg (Video Helper Suite mux).
# BuildKit cache mounts keep apt package indexes/artifacts between rebuilds.
RUN --mount=type=cache,id=comfy-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=comfy-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3.11-venv python3.11-dev python3-pip \
      git curl ca-certificates libgl1 libglib2.0-0 ffmpeg && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# uv — matches host pin. Copy both binaries the uv image ships (`uv` and `uvx`).
COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uvx /usr/local/bin/

# Non-root user with a stable uid.
RUN useradd --uid 1000 --create-home --shell /bin/bash comfy

USER comfy
WORKDIR /workspace

ARG COMFYUI_REF
ARG COMFYUI_GGUF_REF
ARG COMFYUI_WAN_VIDEO_REF
ARG COMFYUI_MMAUDIO_REF
ARG COMFYUI_VHS_REF
ARG COMFYUI_KJ_REF
ARG RGTHREE_REF
ARG COMFYUI_EASY_USE_REF
ARG COMFYUI_NAG_REF
ARG COMFYUI_ADAPTIVE_PROMPTS_REF
ARG COMFYUI_VFI_REF
ARG COMFY_MTB_REF
ARG COMFYUI_COMFYROLL_REF
ARG COMFYUI_MXTOOLKIT_REF

# Clone ComfyUI at the pinned tag/commit.
RUN git clone --depth 1 --branch ${COMFYUI_REF} https://github.com/comfyanonymous/ComfyUI.git

# Clone custom nodes (GGUF + WAN 2.2 Smooth stack). Checkouts are full SHAs from custom-nodes.txt.
WORKDIR /workspace/ComfyUI/custom_nodes
RUN git clone https://github.com/city96/ComfyUI-GGUF.git && \
    (cd ComfyUI-GGUF && git checkout ${COMFYUI_GGUF_REF}) && \
    git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    (cd ComfyUI-WanVideoWrapper && git checkout ${COMFYUI_WAN_VIDEO_REF}) && \
    git clone https://github.com/kijai/ComfyUI-MMAudio.git && \
    (cd ComfyUI-MMAudio && git checkout ${COMFYUI_MMAUDIO_REF}) && \
    git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    (cd ComfyUI-VideoHelperSuite && git checkout ${COMFYUI_VHS_REF}) && \
    git clone https://github.com/kijai/ComfyUI-KJNodes.git && \
    (cd ComfyUI-KJNodes && git checkout ${COMFYUI_KJ_REF}) && \
    git clone https://github.com/rgthree/rgthree-comfy.git && \
    (cd rgthree-comfy && git checkout ${RGTHREE_REF}) && \
    git clone https://github.com/yolain/ComfyUI-Easy-Use.git && \
    (cd ComfyUI-Easy-Use && git checkout ${COMFYUI_EASY_USE_REF}) && \
    git clone https://github.com/scottmudge/ComfyUI-NAG.git && \
    (cd ComfyUI-NAG && git checkout ${COMFYUI_NAG_REF}) && \
    git clone https://github.com/Alectriciti/comfyui-adaptiveprompts.git && \
    (cd comfyui-adaptiveprompts && git checkout ${COMFYUI_ADAPTIVE_PROMPTS_REF}) && \
    git clone https://github.com/GACLove/ComfyUI-VFI.git && \
    (cd ComfyUI-VFI && git checkout ${COMFYUI_VFI_REF}) && \
    git clone https://github.com/melMass/comfy_mtb.git && \
    (cd comfy_mtb && git checkout ${COMFY_MTB_REF}) && \
    git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git && \
    (cd ComfyUI_Comfyroll_CustomNodes && git checkout ${COMFYUI_COMFYROLL_REF}) && \
    git clone https://github.com/Smirnov75/ComfyUI-mxToolkit.git && \
    (cd ComfyUI-mxToolkit && git checkout ${COMFYUI_MXTOOLKIT_REF})

USER root
COPY --chown=comfy:comfy install-custom-node-requirements.sh /workspace/install-custom-node-requirements.sh
RUN chmod +x /workspace/install-custom-node-requirements.sh
USER comfy

# Install deps into a shared venv via uv (faster than pip).
# Cache mount avoids re-downloading torch/cuda wheels on rebuilds.
WORKDIR /workspace/ComfyUI
RUN --mount=type=cache,id=comfy-uv-cache,target=/home/comfy/.cache/uv,uid=1000,gid=1000 \
    uv venv /workspace/.venv --python 3.11 && \
    uv pip install --python /workspace/.venv/bin/python -r requirements.txt && \
    /workspace/install-custom-node-requirements.sh /workspace/.venv/bin/python

# Build-time smoke tests — fail fast if pins drift or repos reorganize.
RUN bash -c 'grep -q "UnetLoaderGGUF" /workspace/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py && \
    grep -q "NODE_CLASS_MAPPINGS" /workspace/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py || \
    (echo "GGUF smoke test: expected class names missing from ComfyUI-GGUF/nodes.py" && exit 1)'
# WanImageToVideo ships with ComfyUI core (comfy_extras/nodes_wan.py); WanVideoWrapper adds sampler/tools nodes.
RUN bash -c 'grep -q "class WanImageToVideo" /workspace/ComfyUI/comfy_extras/nodes_wan.py || \
    (echo "Comfy core smoke test: WanImageToVideo node missing from comfy_extras/nodes_wan.py" && exit 1)'
RUN bash -c 'test -f /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/nodes.py || \
    (echo "WanVideoWrapper smoke test: nodes.py missing" && exit 1)'
RUN bash -c 'grep -rq "KSamplerWithNAG" /workspace/ComfyUI/custom_nodes/ComfyUI-NAG --include="*.py" || \
    (echo "ComfyUI-NAG smoke test: KSamplerWithNAG not found" && exit 1)'
RUN echo "Smoke tests OK: GGUF + core WanImageToVideo + WanVideoWrapper + NAG"

# Entrypoint script.
COPY --chown=comfy:comfy entrypoint.sh /workspace/entrypoint.sh
USER root
RUN chmod +x /workspace/entrypoint.sh
USER comfy

EXPOSE 8188

# start-period is generous: first boot loads torch + cuda kernels.
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8188/system_stats > /dev/null || exit 1

ENTRYPOINT ["/workspace/entrypoint.sh"]
