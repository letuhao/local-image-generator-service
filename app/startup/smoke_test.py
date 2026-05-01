from __future__ import annotations

import asyncio
import time

from app.backends.base import (
    BackendAdapter,
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
)
from app.registry.models import Registry
from app.registry.workflows import (
    find_anchor,
    inject_loras,
    inject_model_source,
    inject_vpred,
    load_workflow,
)
from app.validation import GenerateRequest, resolve_and_validate


class StartupSmokeError(RuntimeError):
    pass


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _build_smoke_payload(model_name: str, seed: int) -> dict[str, object]:
    return {
        "model": model_name,
        "prompt": "startup smoke test",
        "size": "256x256",
        "steps": 1,
        "seed": seed,
        "n": 1,
        "mode": "sync",
        "response_format": "url",
    }


def _ensure_png_outputs(outputs: list[bytes], model_name: str) -> None:
    if not outputs:
        raise StartupSmokeError(f"smoke produced zero outputs for model={model_name!r}")
    for index, image in enumerate(outputs):
        if not image.startswith(_PNG_MAGIC):
            raise StartupSmokeError(
                f"smoke output #{index} for model={model_name!r} is not a PNG payload"
            )


async def _run_one_model_smoke(
    *,
    adapter: BackendAdapter,
    registry: Registry,
    loras_root,
    model_name: str,
    timeout_s: float,
    seed: int,
) -> None:
    payload = _build_smoke_payload(model_name, seed=seed)
    request = GenerateRequest.model_validate(payload)
    validated = resolve_and_validate(
        request,
        registry=registry,
        async_mode_enabled=False,
        loras_root=loras_root,
    )

    graph = load_workflow(validated.model.workflow_path)
    pos_id = find_anchor(graph, "%POSITIVE_PROMPT%")
    neg_id = find_anchor(graph, "%NEGATIVE_PROMPT%")
    ks_id = find_anchor(graph, "%KSAMPLER%")
    graph[pos_id]["inputs"]["text"] = validated.prompt
    graph[neg_id]["inputs"]["text"] = validated.negative_prompt
    ks_inputs = graph[ks_id]["inputs"]
    ks_inputs["seed"] = validated.seed
    ks_inputs["steps"] = validated.steps
    ks_inputs["cfg"] = validated.cfg
    ks_inputs["sampler_name"] = validated.sampler
    ks_inputs["scheduler"] = validated.scheduler
    inject_model_source(graph, model_cfg=validated.model)
    inject_vpred(graph, model_cfg=validated.model)
    inject_loras(graph, validated.loras, model_cfg=validated.model)

    try:
        prompt_id = await adapter.submit(graph)
        await asyncio.wait_for(
            adapter.wait_for_completion(prompt_id, timeout_s=timeout_s),
            timeout=timeout_s,
        )
        outputs = await adapter.fetch_outputs(prompt_id)
    except (ComfyTimeoutError, ComfyUnreachableError, ComfyNodeError) as exc:
        raise StartupSmokeError(f"smoke failed for model={model_name!r}: {exc}") from exc
    except TimeoutError as exc:
        raise StartupSmokeError(
            f"smoke timeout for model={model_name!r} after {timeout_s}s"
        ) from exc
    _ensure_png_outputs(outputs, model_name)


async def run_registry_smoke_tests(
    *,
    adapter: BackendAdapter,
    registry: Registry,
    loras_root,
    timeout_s: float = 120.0,
) -> None:
    names = [
        n
        for n in registry.names()
        if not (registry.get(n).capabilities or {}).get("skip_startup_smoke")
    ]
    if not names:
        raise StartupSmokeError("registry has no models eligible for startup smoke")
    overall_deadline = time.monotonic() + timeout_s
    for index, model_name in enumerate(names):
        remaining = overall_deadline - time.monotonic()
        if remaining <= 0:
            raise StartupSmokeError("startup smoke budget exhausted before all models were tested")
        await _run_one_model_smoke(
            adapter=adapter,
            registry=registry,
            loras_root=loras_root,
            model_name=model_name,
            timeout_s=remaining,
            seed=20260000 + index,
        )
