from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


class BackendError(Exception):
    """Base for every backend-adapter error. Subclasses map to arch §13 enum codes."""

    error_code: str = "internal"


class ComfyUnreachableError(BackendError):
    """ComfyUI HTTP/WS endpoint refused connection or closed mid-call."""

    error_code = "comfy_unreachable"


class ComfyNodeError(BackendError):
    """ComfyUI returned node_errors on submission or history shows execution error."""

    error_code = "comfy_error"


class ComfyTimeoutError(BackendError):
    """JOB_TIMEOUT_S elapsed before completion (WS + polling both exhausted)."""

    error_code = "comfy_timeout"


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Static per-model configuration; loaded from `config/models.yaml` by the Registry."""

    name: str
    backend: Literal["comfyui"]
    workflow_path: str  # relative to repo root (e.g. "workflows/sdxl_eps.json")
    checkpoint: str  # relative to models/ (e.g. "checkpoints/NoobAI-XL-v1.1.safetensors")
    vae: str | None  # None = use checkpoint's baked-in VAE
    vram_estimate_gb: float
    clip_l: str | None = None  # DualCLIP clip_name1 or CLIPLoader clip_name (e.g. FLUX / FLUX.2 TE)
    t5xxl: str | None = None  # DualCLIP clip_name2 (e.g. T5 for FLUX.1)
    dual_clip_type: str | None = None  # DualCLIPLoader type (e.g. "flux"); unused for CLIPLoader graphs
    clip_loader_type: str | None = None  # CLIPLoader type (e.g. "flux2") when workflow uses CLIPLoader
    # Runtime family marker used for workflow/validation branching while keeping
    # a universal client payload contract.
    family: Literal["sdxl", "flux", "qwen", "wan22"] = "sdxl"
    # "eps" | "vpred" — informational in Cycle 3; Cycle 5+ uses it for graph injection.
    prediction: Literal["eps", "vpred"] = "eps"
    capabilities: dict[str, Any] = field(default_factory=dict)  # e.g. {"image_gen": True}
    defaults: dict[str, Any] = field(default_factory=dict)  # size/steps/cfg/sampler/scheduler
    limits: dict[str, Any] = field(default_factory=dict)  # steps_max/n_max/size_max_pixels
    # WAN / ComfyUI-WanVideoWrapper (API workflows with %WAN_*% anchors).
    wan_t5_encoder: str | None = None  # models/text_encoders relative path
    wan_clip_vision: str | None = None  # models/clip_vision (I2V)
    skip_asset_validation: bool = False  # skip on-disk checks (dev/CI without weights)
    # Optional ComfyUI-MMAudio companion workflow (WAN graphs with %MMAUDIO_*% anchors).
    workflow_with_audio: str | None = None  # relative to repo root
    mmaudio_vae: str | None = None  # relative to models/ (mmaudio subfolder weights)
    mmaudio_synchformer: str | None = None
    mmaudio_clip: str | None = None
    mmaudio_diffusion: str | None = None  # MMAudio main checkpoint filename/path under models/


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Output of a successful BackendAdapter.generate() chain."""

    images: list[bytes]  # PNG bytes per output node / image
    prompt_id: str  # the ComfyUI prompt_id (useful for audit)
    duration_ms: float  # wall time from submit to last byte fetched


class BackendAdapter(Protocol):
    """Minimum contract every image-generation backend must implement."""

    async def submit(self, graph: dict) -> str:
        """Send the prompt graph to the backend. Returns a backend-assigned prompt_id."""
        ...

    async def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """Uploads an image to the backend and returns the uploaded filename."""
        ...

    async def wait_for_completion(self, prompt_id: str, timeout_s: float) -> None:
        """Block until the prompt reaches terminal state. Raises ComfyTimeoutError on elapse."""
        ...

    async def fetch_outputs(self, prompt_id: str) -> list[bytes]:
        """Download result PNG bytes (one entry per output image)."""
        ...

    async def cancel(self, prompt_id: str) -> None:
        """Interrupt a running prompt or dequeue a pending one."""
        ...

    async def free(self) -> None:
        """Ask the backend to free VRAM (unload models + free_memory)."""
        ...

    async def unload_models(self, verify_timeout_s: float = 30.0) -> bool:
        """Strict unload for model swap. Returns True when VRAM increase is observed."""
        ...

    async def health(self) -> dict:
        """Return backend health snapshot. `{"status":"ok","vram_free_gb":...}` on ok."""
        ...

    async def close(self) -> None:
        """Release transport resources (HTTP client, WS connection, background tasks)."""
        ...
