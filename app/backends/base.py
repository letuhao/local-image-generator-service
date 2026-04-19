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
    """Static per-model configuration; loaded from `config/models.yaml` in Cycle 3.

    Cycle 2 passes a hand-built literal for tests.
    """

    name: str
    backend: Literal["comfyui"]
    workflow_path: str  # relative to repo root (e.g. "workflows/sdxl_eps.json")
    checkpoint: str  # relative to models/ (e.g. "checkpoints/NoobAI-XL-v1.1.safetensors")
    vae: str | None  # None = use checkpoint's baked-in VAE
    vram_estimate_gb: float
    defaults: dict[str, Any] = field(
        default_factory=dict
    )  # Cycle 3+: size/steps/cfg/sampler/scheduler
    limits: dict[str, Any] = field(
        default_factory=dict
    )  # Cycle 3+: steps_max/n_max/size_max_pixels


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

    async def health(self) -> dict:
        """Return backend health snapshot. `{"status":"ok","vram_free_gb":...}` on ok."""
        ...

    async def close(self) -> None:
        """Release transport resources (HTTP client, WS connection, background tasks)."""
        ...
