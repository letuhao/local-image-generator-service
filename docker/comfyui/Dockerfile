# ComfyUI sidecar for image-gen-service.
#
# Pin sources (must stay in sync — custom-nodes.txt is canonical):
#   docker/comfyui/custom-nodes.txt     ← human-facing doc of pins
#   docker/comfyui/Dockerfile ARG ...   ← default if compose doesn't override
#   docker-compose.yml  build.args      ← actual build-time pins
# When you bump custom-nodes.txt, bump the other two in the same commit.
#
# Base image: nvidia/cuda runtime (not devel) — we install Python + torch wheels,
# not nvcc-compiled extensions. Size target: 4-5 GB.

ARG COMFYUI_REF=v0.9.2
ARG COMFYUI_GGUF_REF=6ea2651e7df66d7585f6ffee804b20e92fb38b8a

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Python 3.11 + git (to clone custom nodes) + curl (healthcheck) + libgl1 (cv2 runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3.11-venv python3.11-dev python3-pip \
      git curl ca-certificates libgl1 libglib2.0-0 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 && \
    rm -rf /var/lib/apt/lists/*

# uv — matches host pin. Copy both binaries the uv image ships (`uv` and `uvx`).
COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uvx /usr/local/bin/

# Non-root user with a stable uid.
RUN useradd --uid 1000 --create-home --shell /bin/bash comfy

USER comfy
WORKDIR /workspace

ARG COMFYUI_REF
ARG COMFYUI_GGUF_REF

# Clone ComfyUI at the pinned tag/commit.
RUN git clone --depth 1 --branch ${COMFYUI_REF} https://github.com/comfyanonymous/ComfyUI.git

# Clone GGUF custom nodes at the pinned SHA.
WORKDIR /workspace/ComfyUI/custom_nodes
RUN git clone https://github.com/city96/ComfyUI-GGUF.git && \
    cd ComfyUI-GGUF && \
    git checkout ${COMFYUI_GGUF_REF}

# Install deps into a shared venv via uv (faster than pip).
WORKDIR /workspace/ComfyUI
RUN uv venv /workspace/.venv --python 3.11 && \
    uv pip install --python /workspace/.venv/bin/python -r requirements.txt && \
    uv pip install --python /workspace/.venv/bin/python -r custom_nodes/ComfyUI-GGUF/requirements.txt

# Build-time smoke test: GGUF custom nodes must declare the expected loader classes.
# We use a textual check rather than an import because the folder name ComfyUI-GGUF
# contains a hyphen (invalid Python module name); ComfyUI itself loads it via path
# manipulation at runtime, but that setup isn't worth replicating at build time.
# If the class names shift upstream, this fails the build fast.
RUN bash -c 'grep -q "UnetLoaderGGUF" /workspace/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py && \
    grep -q "NODE_CLASS_MAPPINGS" /workspace/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py || \
    (echo "GGUF smoke test: expected class names missing from ComfyUI-GGUF/nodes.py" && exit 1)'
RUN echo "GGUF smoke test OK: UnetLoaderGGUF declared, NODE_CLASS_MAPPINGS present"

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
