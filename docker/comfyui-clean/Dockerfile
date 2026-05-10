# Minimal “clean room” base for DIY ComfyUI → build your own image from here.
#
# Includes: Python 3 + venv/pip from the base image (see FROM), ffmpeg, git, curl+wget,
#           basic GL libs. Default user is UID 1000 — `apt` needs root (no sudo in image).
#           One-off packages:  docker compose --profile clean run --rm -u root -it comfyui-clean bash
#           then: apt-get update && apt-get install -y <pkg>
# Does NOT install: PyTorch, ComfyUI, or custom nodes (you add those inside venv).
#
# Base: nvidia/cuda *cudnn-devel* — nvcc + CUDA headers + cuDNN for building CUDA extensions
#       (flash-attn, etc.). Use Docker with NVIDIA Container Toolkit (--gpus all). Pick a
#       PyTorch wheel whose CUDA major matches this image (see pytorch.org), or use a
#       matching PyTorch nightly.
#
# Typical interactive workflow:
#   docker compose --profile clean run --rm -it comfyui-clean bash
#   # If ComfyUI runs in this shell and you want host/LAN port publish: add --service-ports
#   # to `run`, or use `compose up -d comfyui-clean` (see docker-compose `comfyui-clean` ports).
#   cd /workspace && git clone https://github.com/comfyanonymous/ComfyUI.git
#   cd ComfyUI && python3 -m venv .venv && . .venv/bin/activate
#   pip install -U pip wheel
#   # Aligned with CUDA 13.x base image (prefer over cu128 unless you know you need it):
#   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
#   # Runtime-only fallback (CUDA 12.8 Torch build; OK on new drivers but mismatched vs nvcc 13.x):
#   # pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
#   pip install -r requirements.txt
#   # ComfyUI-Manager (pip) — use venv pip only (system pip is PEP 668–blocked):
#   pip install -r /opt/comfyui-clean/pip-extras-essentials.txt
#   # Launch (CRLF-safe entry; host script optional):
#   /opt/comfyui-clean/run-comfyui-entry.sh
#
# Custom nodes — pick one workflow:
#   A) Manager: install/disable from UI once Manager is enabled.
#   B) Git (reproducible):  cd custom_nodes && git clone <repo> && cd <repo> && pip install -r requirements.txt (if present)
#
# Freeze to an image after you finish:
#   docker commit <container_id> my-comfyui:custom
#
# Or copy this Dockerfile, add RUN lines, and `docker build` for reproducible builds.

FROM nvidia/cuda:13.1.2-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-dev \
    python3-pip \
    git \
    curl \
    wget \
    ca-certificates \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Non-root UID 1000 — matches typical bind mounts on the host.
# Recent Ubuntu bases may ship a pre-existing UID 1000 user (avoid duplicate useradd).
RUN if ! getent passwd 1000 >/dev/null; then \
      getent group 1000 >/dev/null || groupadd --gid 1000 comfy; \
      useradd --create-home --uid 1000 --gid 1000 --shell /bin/bash comfy; \
    fi

WORKDIR /workspace
RUN mkdir -p /opt/comfyui-clean && chown 1000:1000 /opt/comfyui-clean

COPY pip-extras-essentials.txt /opt/comfyui-clean/pip-extras-essentials.txt
RUN chmod 644 /opt/comfyui-clean/pip-extras-essentials.txt

# Baked copy (LF normalized at build). Editable host copy is mounted as run-comfyui.host.sh.
COPY run-comfyui.sh /opt/comfyui-clean/run-comfyui.baked.sh
RUN sed -i 's/\r$//' /opt/comfyui-clean/run-comfyui.baked.sh && chmod 755 /opt/comfyui-clean/run-comfyui.baked.sh

COPY run-comfyui-entry.sh /opt/comfyui-clean/run-comfyui-entry.sh
RUN sed -i 's/\r$//' /opt/comfyui-clean/run-comfyui-entry.sh && chmod 755 /opt/comfyui-clean/run-comfyui-entry.sh

RUN chown -R 1000:1000 /workspace

USER 1000:1000

# Keep container alive for `compose run`/CI; override with bash or python main.py
CMD ["bash", "-lc", "trap : TERM INT; tail -f /dev/null"]
