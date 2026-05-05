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
ARG COMFYUI_WAN_VIDEO_REF=df8f3e49daaad117cf3090cc916c83f3d001494c
ARG COMFYUI_MMAUDIO_REF=841b307095f3cdc448d940a5787bc36706e49f1d
ARG COMFYUI_VHS_REF=8923bd836bdab8b7bbdf4ed104b7d045e70c66e2
ARG COMFYUI_KJ_REF=cd5ad8002bdc7d4cc586f69ebc988e22a04a89a1
ARG RGTHREE_REF=84a146fee39f7b3a8c6631dcac29bc13b077eb49
ARG COMFYUI_EASY_USE_REF=de92038f88317699f314be85e5c7af84f1ab9c3a
ARG COMFYUI_NAG_REF=c6f27116a8259f5b501d498a09e51c82fa72e35f
ARG COMFYUI_ADAPTIVE_PROMPTS_REF=aa0d896b5e5d9c7031c3aa995e028f2507dfc63c
ARG COMFYUI_VFI_REF=6176a430f12cd16003f4664c1e3c6af8e96cc3c6
ARG COMFY_MTB_REF=d87e52ea2c112fd95f257dcd6a54a5db77a34fc3
ARG COMFYUI_COMFYROLL_REF=d78b780ae43fcf8c6b7c6505e6ffb4584281ceca
ARG COMFYUI_MXTOOLKIT_REF=7f7a0e584f12078a1c589645d866ae96bad0cc35
# LTX-Video 2.x (10Eros / LTXV family) stack
ARG COMFYUI_LTXVIDEO_REF=2acf7af8991f33b5cc06ec26753cb6e88e057d04
ARG COMFYUI_10S_NODES_REF=f5f8221d9745166e79de775af0949817283f2ea0
ARG COMFYUI_CUSTOM_SCRIPTS_REF=609f3afaa74b2f88ef9ce8d939626065e3247469
ARG COMFYUI_COMFYMATH_REF=c01177221c31b8e5fbc062778fc8254aeb541638
ARG COMFYUI_DETAIL_DAEMON_REF=39206d10849584e0b6ded943faca4dcd8747beb7
ARG COMFYUI_AUDIOTOOLS_REF=8763c3ec71d4d05278b53386cec57a06e1d99417
# RTX VSR / DaSiWa / WhiteRabbit (see docker/comfyui/custom-nodes.txt)
ARG NVIDIA_RTX_NODES_REF=892515e3eb9a4920a131a502a047e47adca9eb0d
ARG COMFYUI_DASIWA_REF=a24d041df8f1b36fcd453341f65a636e76303d0f
ARG COMFYUI_WHITERABBIT_REF=178156241b29b7cf0fb540a9688e1d9c375e8608
ARG RES4LYF_REF=0dc91c00c4c3fb38e7874fcd7a2a327765e8882c
ARG COMFYUI_CONTROLNET_AUX_REF=e8b689a513c3e6b63edc44066560ca5919c0576e
ARG COMFYUI_SEGMENT_ANYTHING_2_REF=0c35fff5f382803e2310103357b5e985f5437f32
ARG COMFYUI_ESSENTIALS_REF=9d9f4bedfc9f0321c19faf71855e228c93bd0dc9
ARG COMFYUI_LAYERSTYLE_REF=d94bef1ee5ed3656f5ff1bb2830a4ffd94f40935
ARG COMFYUI_LAYERSTYLE_ADV_REF=7b678b401a38307a4a7e99ef6630bd48f9cecedb
ARG COMFYUI_IMPACT_PACK_REF=429d0159ad429e64d2b3916e6e7be9c22d025c3c
ARG COMFYUI_LOGIC_REF=f82d46766b26788c17dbbbd6f2421af23c267666
ARG COMFYUI_RMBG_REF=d7402513f23f58db7d56754b02a4f51a148b4941
ARG COMFYUI_SEEDVR2_REF=4490bd1f482e026674543386bb2a4d176da245b9
ARG COMFYUI_OLLAMA_REF=6db7560576e5a59488708e6be13e07b5aba2432a
ARG COMFYUI_OPENAI_API_REF=efb68c42291ae77df665423e02d6885a3f556bd1
ARG COMFYUI_OPENAI_WORKFLOW_REF=89e2e57b02d3865aa9349076d2f373513e8afd0b
ARG COMFYUI_CHIBI_NODES_REF=9a9734f9c17bab3b4775c04291d26f48cbff2791
ARG COMFYUI_FIND_PERFECT_RES_REF=b8ee6c1872e796329bffbcbf53a5207aaf964bdb
ARG COMFYUI_FILL_NODES_REF=5bae402eadb57a795541825c3fa7fdf4457aee59
ARG COMFYUI_PAINTER_I2V_ADV_REF=a044ac7b2a33565343ddc75283c94a41371b4352
ARG COMFYUI_PAINTER_LONG_VIDEO_REF=889b4ff67909561e52d6ae023f5b9e8c33fdba94
ARG WAS_NODE_SUITE_REF=ea935d1044ae5a26efa54ebeb18fe9020af49a45

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
      git curl ca-certificates libgl1 libglib2.0-0 ffmpeg libsndfile1 \
      build-essential && \
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
ARG COMFYUI_LTXVIDEO_REF
ARG COMFYUI_10S_NODES_REF
ARG COMFYUI_CUSTOM_SCRIPTS_REF
ARG COMFYUI_COMFYMATH_REF
ARG COMFYUI_DETAIL_DAEMON_REF
ARG COMFYUI_AUDIOTOOLS_REF
ARG NVIDIA_RTX_NODES_REF
ARG COMFYUI_DASIWA_REF
ARG COMFYUI_WHITERABBIT_REF
ARG RES4LYF_REF
ARG COMFYUI_CONTROLNET_AUX_REF
ARG COMFYUI_SEGMENT_ANYTHING_2_REF
ARG COMFYUI_ESSENTIALS_REF
ARG COMFYUI_LAYERSTYLE_REF
ARG COMFYUI_LAYERSTYLE_ADV_REF
ARG COMFYUI_IMPACT_PACK_REF
ARG COMFYUI_LOGIC_REF
ARG COMFYUI_RMBG_REF
ARG COMFYUI_SEEDVR2_REF
ARG COMFYUI_OLLAMA_REF
ARG COMFYUI_OPENAI_API_REF
ARG COMFYUI_OPENAI_WORKFLOW_REF
ARG COMFYUI_CHIBI_NODES_REF
ARG COMFYUI_FIND_PERFECT_RES_REF
ARG COMFYUI_FILL_NODES_REF
ARG COMFYUI_PAINTER_I2V_ADV_REF
ARG COMFYUI_PAINTER_LONG_VIDEO_REF
ARG WAS_NODE_SUITE_REF

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
    (cd ComfyUI-mxToolkit && git checkout ${COMFYUI_MXTOOLKIT_REF}) && \
    git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git && \
    (cd ComfyUI-LTXVideo && git checkout ${COMFYUI_LTXVIDEO_REF}) && \
    git clone https://github.com/TenStrip/10S-Comfy-nodes.git && \
    (cd 10S-Comfy-nodes && git checkout ${COMFYUI_10S_NODES_REF}) && \
    git clone https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git && \
    (cd ComfyUI-Custom-Scripts && git checkout ${COMFYUI_CUSTOM_SCRIPTS_REF}) && \
    git clone https://github.com/evanspearman/ComfyMath.git && \
    (cd ComfyMath && git checkout ${COMFYUI_COMFYMATH_REF}) && \
    git clone https://github.com/Jonseed/ComfyUI-Detail-Daemon.git && \
    (cd ComfyUI-Detail-Daemon && git checkout ${COMFYUI_DETAIL_DAEMON_REF}) && \
    git clone https://github.com/Urabewe/ComfyUI-AudioTools.git && \
    (cd ComfyUI-AudioTools && git checkout ${COMFYUI_AUDIOTOOLS_REF}) && \
    git clone https://github.com/Comfy-Org/Nvidia_RTX_Nodes_ComfyUI.git && \
    (cd Nvidia_RTX_Nodes_ComfyUI && git checkout ${NVIDIA_RTX_NODES_REF}) && \
    git clone https://github.com/darksidewalker/ComfyUI-DaSiWa-Nodes.git && \
    (cd ComfyUI-DaSiWa-Nodes && git checkout ${COMFYUI_DASIWA_REF}) && \
    git clone https://github.com/Artificial-Sweetener/comfyui-WhiteRabbit.git && \
    (cd comfyui-WhiteRabbit && git checkout ${COMFYUI_WHITERABBIT_REF}) && \
    git clone https://github.com/ClownsharkBatwing/RES4LYF.git && \
    (cd RES4LYF && git checkout ${RES4LYF_REF}) && \
    git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git && \
    (cd comfyui_controlnet_aux && git checkout ${COMFYUI_CONTROLNET_AUX_REF}) && \
    git clone https://github.com/kijai/ComfyUI-segment-anything-2.git && \
    (cd ComfyUI-segment-anything-2 && git checkout ${COMFYUI_SEGMENT_ANYTHING_2_REF}) && \
    git clone https://github.com/cubiq/ComfyUI_essentials.git && \
    (cd ComfyUI_essentials && git checkout ${COMFYUI_ESSENTIALS_REF}) && \
    git clone https://github.com/chflame163/ComfyUI_LayerStyle.git && \
    (cd ComfyUI_LayerStyle && git checkout ${COMFYUI_LAYERSTYLE_REF}) && \
    git clone https://github.com/chflame163/ComfyUI_LayerStyle_Advance.git && \
    (cd ComfyUI_LayerStyle_Advance && git checkout ${COMFYUI_LAYERSTYLE_ADV_REF}) && \
    git clone https://github.com/ltdrdata/ComfyUI-Impact-Pack.git && \
    (cd ComfyUI-Impact-Pack && git checkout ${COMFYUI_IMPACT_PACK_REF}) && \
    git clone https://github.com/theUpsider/ComfyUI-Logic.git && \
    (cd ComfyUI-Logic && git checkout ${COMFYUI_LOGIC_REF}) && \
    git clone https://github.com/1038lab/ComfyUI-RMBG.git && \
    (cd ComfyUI-RMBG && git checkout ${COMFYUI_RMBG_REF}) && \
    git clone https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git && \
    (cd ComfyUI-SeedVR2_VideoUpscaler && git checkout ${COMFYUI_SEEDVR2_REF}) && \
    git clone https://github.com/stavsap/comfyui-ollama.git && \
    (cd comfyui-ollama && git checkout ${COMFYUI_OLLAMA_REF}) && \
    git clone https://github.com/hekmon/comfyui-openai-api.git && \
    (cd comfyui-openai-api && git checkout ${COMFYUI_OPENAI_API_REF}) && \
    git clone https://github.com/ComfyUI-Workflow/ComfyUI-OpenAI.git && \
    (cd ComfyUI-OpenAI && git checkout ${COMFYUI_OPENAI_WORKFLOW_REF}) && \
    git clone https://github.com/chibiace/ComfyUI-Chibi-Nodes.git && \
    (cd ComfyUI-Chibi-Nodes && git checkout ${COMFYUI_CHIBI_NODES_REF}) && \
    git clone https://github.com/ashtar1984/comfyui-find-perfect-resolution.git && \
    (cd comfyui-find-perfect-resolution && git checkout ${COMFYUI_FIND_PERFECT_RES_REF}) && \
    git clone https://github.com/filliptm/ComfyUI_Fill-Nodes.git && \
    (cd ComfyUI_Fill-Nodes && git checkout ${COMFYUI_FILL_NODES_REF}) && \
    git clone https://github.com/princepainter/ComfyUI-PainterI2Vadvanced.git && \
    (cd ComfyUI-PainterI2Vadvanced && git checkout ${COMFYUI_PAINTER_I2V_ADV_REF}) && \
    git clone https://github.com/princepainter/ComfyUI-PainterLongVideo.git && \
    (cd ComfyUI-PainterLongVideo && git checkout ${COMFYUI_PAINTER_LONG_VIDEO_REF}) && \
    git clone https://github.com/WASasquatch/was-node-suite-comfyui.git && \
    (cd was-node-suite-comfyui && git checkout ${WAS_NODE_SUITE_REF})

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
    /workspace/install-custom-node-requirements.sh /workspace/.venv/bin/python && \
    uv pip install --python /workspace/.venv/bin/python \
      sageattention \
      onnx \
      onnxruntime-gpu

# Build-time smoke tests — fail fast if pins drift or repos reorganize.
RUN bash -c 'grep -q "UnetLoaderGGUF" /workspace/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py && \
    grep -q "NODE_CLASS_MAPPINGS" /workspace/ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py || \
    (echo "GGUF smoke test: expected class names missing from ComfyUI-GGUF/nodes.py" && exit 1)'
# WanImageToVideo ships with ComfyUI core (comfy_extras/nodes_wan.py); WanVideoWrapper adds sampler/tools nodes.
RUN bash -c 'grep -q "class WanImageToVideo" /workspace/ComfyUI/comfy_extras/nodes_wan.py || \
    (echo "Comfy core smoke test: WanImageToVideo node missing from comfy_extras/nodes_wan.py" && exit 1)'
RUN bash -c 'test -f /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/nodes.py || \
    (echo "WanVideoWrapper smoke test: nodes.py missing" && exit 1)'
RUN bash -c 'grep -q "NormalizeAudioLoudness" /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/nodes_utility.py || \
    (echo "WanVideoWrapper smoke test: NormalizeAudioLoudness missing from nodes_utility.py" && exit 1)'
RUN bash -c 'grep -q "WanChunkFeedForward" /workspace/ComfyUI/custom_nodes/ComfyUI-KJNodes/__init__.py && \
    grep -q "ColorMatchV2" /workspace/ComfyUI/custom_nodes/ComfyUI-KJNodes/__init__.py || \
    (echo "ComfyUI-KJNodes smoke test: WanChunkFeedForward / ColorMatchV2 missing from NODE_CONFIG" && exit 1)'
RUN bash -c 'grep -rq "KSamplerWithNAG" /workspace/ComfyUI/custom_nodes/ComfyUI-NAG --include="*.py" || \
    (echo "ComfyUI-NAG smoke test: KSamplerWithNAG not found" && exit 1)'
RUN bash -c 'grep -rq "NODE_CLASS_MAPPINGS" /workspace/ComfyUI/custom_nodes/ComfyUI-Detail-Daemon --include="*.py" || \
    (echo "ComfyUI-Detail-Daemon smoke test: NODE_CLASS_MAPPINGS not found under extension" && exit 1)'
RUN bash -c 'test -f /workspace/ComfyUI/custom_nodes/ComfyUI-AudioTools/audio_normalize.py || \
    (echo "ComfyUI-AudioTools smoke test: audio_normalize.py missing" && exit 1)'
RUN bash -c 'grep -q "RTXVideoSuperResolution" /workspace/ComfyUI/custom_nodes/Nvidia_RTX_Nodes_ComfyUI/__init__.py || \
    (echo "Nvidia_RTX_Nodes_ComfyUI smoke test: RTXVideoSuperResolution missing" && exit 1)'
RUN bash -c 'grep -rq "DaSiWa_ResolutionScaleCalculator" /workspace/ComfyUI/custom_nodes/ComfyUI-DaSiWa-Nodes --include="*.py" || \
    (echo "ComfyUI-DaSiWa-Nodes smoke test: DaSiWa_ResolutionScaleCalculator missing" && exit 1)'
RUN bash -c 'grep -q "NODE_CLASS_MAPPINGS" /workspace/ComfyUI/custom_nodes/comfyui-WhiteRabbit/__init__.py || \
    (echo "comfyui-WhiteRabbit smoke test: NODE_CLASS_MAPPINGS missing" && exit 1)'
RUN bash -c 'grep -q "ClownModelLoader" /workspace/ComfyUI/custom_nodes/RES4LYF/__init__.py || \
    (echo "RES4LYF smoke test: ClownModelLoader mapping missing" && exit 1)'
RUN bash -c 'grep -q "AIO_Preprocessor" /workspace/ComfyUI/custom_nodes/comfyui_controlnet_aux/__init__.py && \
    grep -rq "DepthAnythingV2Preprocessor" /workspace/ComfyUI/custom_nodes/comfyui_controlnet_aux --include="*.py" || \
    (echo "comfyui_controlnet_aux smoke test: AIO_Preprocessor / DepthAnythingV2Preprocessor missing" && exit 1)'
RUN bash -c 'grep -q "\"DWPreprocessor\"" /workspace/ComfyUI/custom_nodes/comfyui_controlnet_aux/node_wrappers/dwpose.py && \
    grep -q "\"PixelPerfectResolution\"" /workspace/ComfyUI/custom_nodes/comfyui_controlnet_aux/hint_image_enchance.py || \
    (echo "comfyui_controlnet_aux smoke test: DWPreprocessor / PixelPerfectResolution missing" && exit 1)'
RUN bash -c 'grep -q "DownloadAndLoadSAM2Model" /workspace/ComfyUI/custom_nodes/ComfyUI-segment-anything-2/nodes.py && \
    grep -q "Sam2Segmentation" /workspace/ComfyUI/custom_nodes/ComfyUI-segment-anything-2/nodes.py || \
    (echo "ComfyUI-segment-anything-2 smoke test: DownloadAndLoadSAM2Model / Sam2Segmentation missing" && exit 1)'
RUN bash -c 'grep -q "SimpleMath+" /workspace/ComfyUI/custom_nodes/ComfyUI_essentials/misc.py || \
    (echo "ComfyUI_essentials smoke test: SimpleMath+ missing" && exit 1)'
RUN bash -c 'grep -rq "ImageScaleByAspectRatio V2" /workspace/ComfyUI/custom_nodes/ComfyUI_LayerStyle --include="*.py" || \
    (echo "ComfyUI_LayerStyle smoke test: ImageScaleByAspectRatio V2 missing" && exit 1)'
RUN bash -c 'grep -rq "GeminiV2" /workspace/ComfyUI/custom_nodes/ComfyUI_LayerStyle_Advance --include="*.py" || \
    (echo "ComfyUI_LayerStyle_Advance smoke test: GeminiV2 missing" && exit 1)'
RUN bash -c 'grep -rq "ImpactSwitch" /workspace/ComfyUI/custom_nodes/ComfyUI-Impact-Pack --include="*.py" || \
    (echo "ComfyUI-Impact-Pack smoke test: ImpactSwitch missing" && exit 1)'
RUN bash -c 'grep -rq "\"Float\"" /workspace/ComfyUI/custom_nodes/ComfyUI-Logic --include="*.py" && \
    grep -rq "\"Int\"" /workspace/ComfyUI/custom_nodes/ComfyUI-Logic --include="*.py" || \
    (echo "ComfyUI-Logic smoke test: Float / Int missing" && exit 1)'
RUN bash -c 'grep -rq "RMBG" /workspace/ComfyUI/custom_nodes/ComfyUI-RMBG --include="*.py" || \
    (echo "ComfyUI-RMBG smoke test: RMBG missing" && exit 1)'
RUN bash -c 'grep -rq "SeedVR2" /workspace/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler --include="*.py" || \
    (echo "ComfyUI-SeedVR2 smoke test: SeedVR2 missing" && exit 1)'
RUN bash -c 'grep -rq "OllamaConnectivityV2" /workspace/ComfyUI/custom_nodes/comfyui-ollama --include="*.py" && \
    grep -rq "OllamaGenerateV2" /workspace/ComfyUI/custom_nodes/comfyui-ollama --include="*.py" || \
    (echo "comfyui-ollama smoke test: OllamaConnectivityV2 / OllamaGenerateV2 missing" && exit 1)'
RUN bash -c 'grep -rq "OpenAI" /workspace/ComfyUI/custom_nodes/comfyui-openai-api --include="*.py" || \
    (echo "comfyui-openai-api smoke test: OpenAI node mappings missing" && exit 1)'
RUN bash -c 'grep -rq "OpenAI" /workspace/ComfyUI/custom_nodes/ComfyUI-OpenAI --include="*.py" || \
    (echo "ComfyUI-OpenAI smoke test: OpenAI nodes missing" && exit 1)'
RUN bash -c 'grep -q "RandomResolutionLatent" /workspace/ComfyUI/custom_nodes/ComfyUI-Chibi-Nodes/__init__.py || \
    (echo "ComfyUI-Chibi-Nodes smoke test: expected node mappings missing" && exit 1)'
RUN bash -c 'grep -rq "FindPerfectResolution" /workspace/ComfyUI/custom_nodes/comfyui-find-perfect-resolution --include="*.py" || \
    (echo "comfyui-find-perfect-resolution smoke test: FindPerfectResolution missing" && exit 1)'
RUN bash -c 'grep -rq "FL_RIFE" /workspace/ComfyUI/custom_nodes/ComfyUI_Fill-Nodes --include="*.py" || \
    (echo "ComfyUI_Fill-Nodes smoke test: FL_RIFE missing" && exit 1)'
RUN bash -c 'grep -rq "PainterI2VAdvanced" /workspace/ComfyUI/custom_nodes/ComfyUI-PainterI2Vadvanced --include="*.py" || \
    (echo "ComfyUI-PainterI2Vadvanced smoke test: PainterI2VAdvanced missing" && exit 1)'
RUN bash -c 'grep -rq "PainterLongVideo" /workspace/ComfyUI/custom_nodes/ComfyUI-PainterLongVideo --include="*.py" || \
    (echo "ComfyUI-PainterLongVideo smoke test: PainterLongVideo missing" && exit 1)'
RUN bash -c 'grep -rq "Text Concatenate" /workspace/ComfyUI/custom_nodes/was-node-suite-comfyui --include="*.py" || \
    (echo "was-node-suite smoke test: Text Concatenate missing" && exit 1)'
RUN echo "Smoke tests OK: GGUF + Wan stack + RTX/DaSiWa/WhiteRabbit/RES4LYF + controlnet_aux + SAM2 + essentials + layerstyle + impact + logic + rmbg + seedvr2 + ollama + openai-api + openai-workflow + chibi + perfect-resolution + fill-nodes + painter-i2v + painter-longvideo + WAS"

# extra_model_paths.yaml: let LTXVAudioVAELoader find standalone audio VAE files.
# ComfyUI-LTXVideo's LTXVAudioVAELoader only searches the "checkpoints" folder_paths
# group.  Adding the vae/ subdirectory here makes LTX23_audio_vae_bf16.safetensors
# findable without duplicating the file.
RUN printf 'other_ui:\n  base_path: /workspace/ComfyUI/models\n  checkpoints: vae\n' \
    > /workspace/ComfyUI/extra_model_paths.yaml

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
