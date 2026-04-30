from __future__ import annotations

import asyncio
import base64
import copy
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from app.backends.base import (
    BackendAdapter,
    BackendError,
    ComfyNodeError,
    ComfyTimeoutError,
    ComfyUnreachableError,
    ModelConfig,
)
from app.monitoring.metrics import MonitoringMetrics
from app.queue.jobs import (
    Job,
    set_completed,
    set_failed,
    set_running,
)
from app.postprocess.transparency import apply_transparent_background
from app.queue.store import JobStore
from app.registry.models import Registry
from app.registry.workflows import (
    find_anchor,
    inject_loras,
    inject_model_source,
    inject_vpred,
    load_workflow,
)
from app.storage.s3 import StorageError
from app.validation import (
    GenerateRequest,
    ValidationFailureError,
    resolve_and_validate,
    touch_last_used_async,
)

log = structlog.get_logger(__name__)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class JobResult:
    """Worker → handler payload when a sync request's future resolves."""

    data: list[dict[str, Any]]  # response data[] entries (url or b64_json)
    duration_ms: float
    resolved_seed: int  # the integer seed actually sent to the sampler


def _raise_if_not_png(data: bytes) -> None:
    if not data or not data.startswith(_PNG_MAGIC):
        raise ComfyNodeError(f"non-PNG bytes from ComfyUI (first={data[:8]!r})")


class _WorkerItem:
    """Internal queue item carrying the job + optional future for handler."""

    __slots__ = ("future", "job")

    def __init__(self, job: Job, future: asyncio.Future[JobResult] | None) -> None:
        self.job = job
        self.future = future


class QueueWorker:
    """Single-task GPU-work serializer. Arch §4.2.

    Re-validates `job.input_json` on every dequeue so recovered jobs (Cycle 4
    restart recovery) and fresh handler enqueues share ONE code path.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        adapter: BackendAdapter,
        s3: Any,  # duck-typed: S3Storage or test fake
        registry: Registry,
        public_base_url: str,
        job_timeout_s: float,
        max_queue: int,
        loras_root: Path,
        async_mode_enabled: bool = False,
        metrics: MonitoringMetrics | None = None,
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._s3 = s3
        self._registry = registry
        self._public_base_url = public_base_url
        self._job_timeout_s = job_timeout_s
        self._async_mode_enabled = async_mode_enabled
        self._loras_root = loras_root
        self._queue: asyncio.Queue[_WorkerItem] = asyncio.Queue(maxsize=max_queue)
        self._last_model_name: str | None = None
        self._metrics = metrics

    # ───────────────────────── enqueue ─────────────────────────

    async def enqueue(self, job: Job) -> asyncio.Future[JobResult]:
        """Handler path: put the job + a fresh future on the queue, return it.

        Blocks on capacity — under steady state the SQLite `count_active` gate
        in the handler prevents this from ever blocking.
        """
        fut: asyncio.Future[JobResult] = asyncio.get_running_loop().create_future()
        await self._queue.put(_WorkerItem(job, fut))
        return fut

    async def enqueue_detached(self, job: Job) -> None:
        """Pure-async POST path: worker runs the pipeline with no handler Future."""
        await self._queue.put(_WorkerItem(job, None))

    async def enqueue_recovery(self, job: Job) -> None:
        """Boot recovery path: no future, no handler waiting. Uses blocking put
        so asyncio.Queue capacity is honored. Worker task MUST be consuming
        already for this to not deadlock (lifespan spawns worker BEFORE recovery)."""
        await self._queue.put(_WorkerItem(job, None))

    def set_registry(self, registry: Registry) -> None:
        """Swap runtime registry reference (used by admin hot-reload)."""
        self._registry = registry

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def last_model_name(self) -> str | None:
        return self._last_model_name

    # ───────────────────────── main loop ─────────────────────────

    async def run(self) -> None:
        """Serve items until cancelled. Never raises from the loop."""
        log.info("queue_worker.started")
        try:
            while True:
                item = await self._queue.get()
                await self._process_one(item)
        except asyncio.CancelledError:
            log.info("queue_worker.cancelled", pending=self._queue.qsize())
            raise

    async def _process_one(self, item: _WorkerItem) -> None:
        job = item.job
        fut = item.future
        structlog.contextvars.bind_contextvars(job_id=job.id)
        started = time.perf_counter()
        try:
            result = await self._run_pipeline(job)
        except (BackendError, StorageError) as exc:
            # DB state already recorded by _run_pipeline's per-step set_failed.
            if self._metrics is not None:
                error_code = exc.__class__.__name__.lower()
                self._metrics.record_job_event(status="failed", error_code=error_code)
            if fut is not None and not fut.done():
                fut.set_exception(exc)
        except Exception as exc:  # pragma: no cover — defensive
            log.exception("queue_worker.unexpected", job_id=job.id)
            await set_failed(self._store, job.id, error_code="internal", error_message=str(exc))
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="internal")
            if fut is not None and not fut.done():
                fut.set_exception(exc)
        else:
            if self._metrics is not None:
                self._metrics.record_job_event(status="completed")
            if fut is not None and not fut.done():
                fut.set_result(result)
            log.info(
                "queue_worker.completed",
                job_id=job.id,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            structlog.contextvars.unbind_contextvars("job_id")

    # ───────────────────────── defensive cleanup helpers ─────────────────────────

    async def _safe_cancel(self, prompt_id: str) -> None:
        """Best-effort adapter.cancel(prompt_id). Swallows transport errors —
        caller is already in an error-recovery path and shouldn't mask the primary
        failure with a cleanup failure."""
        try:
            await self._adapter.cancel(prompt_id)
            log.info("queue_worker.cancel_ok", prompt_id=prompt_id)
        except Exception as exc:
            log.warning("queue_worker.cancel_failed", prompt_id=prompt_id, error=str(exc))

    async def _safe_free(self) -> None:
        """Best-effort adapter.free(). Same swallow posture as _safe_cancel."""
        try:
            await self._adapter.free()
            log.info("queue_worker.free_ok")
        except Exception as exc:
            log.warning("queue_worker.free_failed", error=str(exc))

    async def _has_swap_headroom(self, next_model: ModelConfig) -> bool:
        """Allow swap when current free VRAM is already sufficient.

        `/free` verification is based on "vram_free increased". On some CUDA
        allocator paths, free VRAM can remain flat after unload even when
        enough headroom already exists for the next model.
        """
        health_fn = getattr(self._adapter, "health", None)
        if not callable(health_fn):
            return False
        try:
            snapshot = await health_fn()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("queue_worker.swap_headroom_health_failed", error=str(exc))
            return False

        if snapshot.get("status") != "ok":
            return False

        free_gb_raw = snapshot.get("vram_free_gb")
        try:
            free_gb = float(free_gb_raw)
        except (TypeError, ValueError):
            return False

        required_gb = float(next_model.vram_estimate_gb) + 0.5
        ok = free_gb >= required_gb
        log.info(
            "queue_worker.swap_headroom_check",
            model=next_model.name,
            free_gb=round(free_gb, 3),
            required_gb=round(required_gb, 3),
            ok=ok,
        )
        return ok

    # ───────────────────────── pipeline (used by _process_one) ─────────────────────────

    async def _run_pipeline(self, job: Job) -> JobResult:
        """Full graph-prep → submit → wait → fetch → upload chain.

        Persists DB state at every transition. Raises BackendError on any
        sub-step failure; _process_one maps the error to DB state + future.
        """
        # 1. Re-parse + re-resolve validation.
        try:
            raw = json.loads(job.input_json)
            body = GenerateRequest.model_validate(raw)
            validated = resolve_and_validate(
                body,
                registry=self._registry,
                async_mode_enabled=self._async_mode_enabled,
                loras_root=self._loras_root,
            )
            await touch_last_used_async(self._loras_root, validated.loras)
        except ValidationFailureError as exc:
            await set_failed(
                self._store, job.id, error_code=exc.error_code, error_message=exc.message
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code=exc.error_code)
            raise ComfyNodeError(f"re-validation failed: {exc}") from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            await set_failed(
                self._store,
                job.id,
                error_code="validation_error",
                error_message=f"re-validation of stored input_json failed: {exc}",
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="validation_error")
            raise ComfyNodeError(f"re-validation failed: {exc}") from exc

        # 2. Prepare graph from workflow template.
        graph_template = load_workflow(validated.model.workflow_path)
        graph = copy.deepcopy(graph_template)

        pos_id = find_anchor(graph, "%POSITIVE_PROMPT%")
        neg_id = find_anchor(graph, "%NEGATIVE_PROMPT%")
        ks_id = find_anchor(graph, "%KSAMPLER%")
        graph[pos_id]["inputs"]["text"] = validated.prompt
        graph[neg_id]["inputs"]["text"] = validated.negative_prompt
        ks_in = graph[ks_id]["inputs"]

        actual_seed = validated.seed if validated.seed >= 0 else secrets.randbelow(2**53)
        ks_in["seed"] = actual_seed
        ks_in["steps"] = validated.steps
        ks_in["cfg"] = validated.cfg
        ks_in["sampler_name"] = validated.sampler
        ks_in["scheduler"] = validated.scheduler

        latent_nodes = [
            nid for nid, node in graph.items() if node.get("class_type") == "EmptyLatentImage"
        ]
        if len(latent_nodes) > 1:
            log.warning("queue_worker.multiple_latent_nodes", count=len(latent_nodes))
        for nid in latent_nodes[:1]:
            graph[nid]["inputs"]["width"] = validated.width
            graph[nid]["inputs"]["height"] = validated.height
            graph[nid]["inputs"]["batch_size"] = validated.n

        # 2b. Inject runtime model sources first so template hardcoded source refs
        # (checkpoint/vae/encoders) do not override the selected model ID.
        inject_model_source(graph, model_cfg=validated.model)

        # 2c. v-prediction scaffold (no-op for eps) + LoRA chain injection.
        # Order matters: vpred first (may reshape model-source outputs once
        # implemented), then inject_loras consumes the final MODEL/CLIP anchors.
        inject_vpred(graph, model_cfg=validated.model)
        inject_loras(graph, validated.loras, model_cfg=validated.model)

        # 3. Enforce model-swap unload before submit.
        current_model = validated.model.name
        if self._last_model_name is not None and self._last_model_name != current_model:
            unloaded = await self._adapter.unload_models(verify_timeout_s=30.0)
            if not unloaded:
                if await self._has_swap_headroom(validated.model):
                    log.warning(
                        "queue_worker.swap_unload_unverified_but_headroom_ok",
                        previous_model=self._last_model_name,
                        next_model=current_model,
                    )
                else:
                    msg = (
                        "model swap refused: /free did not increase vram_free within 30s "
                        f"({self._last_model_name} -> {current_model})"
                    )
                    await set_failed(
                        self._store,
                        job.id,
                        error_code="vram_budget_exceeded",
                        error_message=msg,
                    )
                    if self._metrics is not None:
                        self._metrics.record_job_event(
                            status="failed", error_code="vram_budget_exceeded"
                        )
                    raise ComfyNodeError(msg)

        # 4. Submit to ComfyUI + update DB.
        try:
            prompt_id = await self._adapter.submit(graph)
        except ComfyUnreachableError as exc:
            await set_failed(
                self._store, job.id, error_code="comfy_unreachable", error_message=str(exc)
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="comfy_unreachable")
            raise
        except ComfyNodeError as exc:
            await set_failed(self._store, job.id, error_code="comfy_error", error_message=str(exc))
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="comfy_error")
            raise

        # set_running failure would leave ComfyUI running an untracked prompt —
        # on SQLite write error, cancel the prompt so the GPU isn't stuck on
        # work we can't account for.
        try:
            await set_running(
                self._store,
                job.id,
                prompt_id=prompt_id,
                client_id=getattr(self._adapter, "client_id", "unknown"),
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="running")
        except Exception as exc:
            log.exception("queue_worker.set_running_failed", job_id=job.id, prompt_id=prompt_id)
            await self._safe_cancel(prompt_id)
            raise ComfyNodeError(f"set_running failed: {exc}") from exc

        # 5. Wait + fetch.
        start_gen = time.perf_counter()
        wait_timeout_s = float(
            validated.timeout_s
            if validated.timeout_s is not None
            else validated.model.defaults.get("job_timeout_s", self._job_timeout_s)
        )
        try:
            await self._adapter.wait_for_completion(prompt_id, timeout_s=wait_timeout_s)
        except ComfyTimeoutError as exc:
            # Arch §12: on timeout, interrupt + free VRAM before surrendering.
            await self._safe_cancel(prompt_id)
            await self._safe_free()
            await set_failed(
                self._store, job.id, error_code="comfy_timeout", error_message=str(exc)
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="comfy_timeout")
            raise
        except ComfyUnreachableError as exc:
            await set_failed(
                self._store, job.id, error_code="comfy_unreachable", error_message=str(exc)
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="comfy_unreachable")
            raise

        try:
            images = await self._adapter.fetch_outputs(prompt_id)
        except ComfyUnreachableError as exc:
            await set_failed(
                self._store, job.id, error_code="comfy_unreachable", error_message=str(exc)
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="comfy_unreachable")
            raise

        # 6. Validate bytes + upload. Zero-output or malformed PNG means ComfyUI
        # produced something we can't use — classify as `comfy_error` per arch §13.
        if not images:
            msg = "ComfyUI returned zero outputs"
            await set_failed(self._store, job.id, error_code="comfy_error", error_message=msg)
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="comfy_error")
            raise ComfyNodeError(msg)

        processed_images: list[bytes] = []
        for png in images:
            try:
                _raise_if_not_png(png)
            except ComfyNodeError as exc:
                await set_failed(
                    self._store, job.id, error_code="comfy_error", error_message=str(exc)
                )
                if self._metrics is not None:
                    self._metrics.record_job_event(status="failed", error_code="comfy_error")
                raise
            if validated.transparent_background:
                try:
                    png = apply_transparent_background(png)
                except Exception as exc:
                    msg = f"transparent postprocess failed: {exc}"
                    await set_failed(
                        self._store, job.id, error_code="comfy_error", error_message=msg
                    )
                    if self._metrics is not None:
                        self._metrics.record_job_event(status="failed", error_code="comfy_error")
                    raise ComfyNodeError(msg) from exc
            processed_images.append(png)

        images = processed_images

        output_keys: list[str] = []
        try:
            for idx, png in enumerate(images):
                bucket, key = await self._s3.upload_png(job.id, idx, png)
                output_keys.append(f"{bucket}/{key}")
        except StorageError as exc:
            await set_failed(
                self._store, job.id, error_code="storage_error", error_message=str(exc)
            )
            if self._metrics is not None:
                self._metrics.record_job_event(status="failed", error_code="storage_error")
            raise  # handler maps StorageError → 502 storage_error per Cycle 3

        # 7. Build response data.
        data: list[dict[str, Any]] = []
        if validated.response_format == "b64_json":
            for png in images:
                data.append({"b64_json": base64.b64encode(png).decode("ascii")})
        else:
            for idx in range(len(images)):
                data.append({"url": f"{self._public_base_url}/v1/images/{job.id}/{idx}.png"})

        duration_ms = (time.perf_counter() - start_gen) * 1000
        await set_completed(
            self._store,
            job.id,
            output_keys=output_keys,
            result_json=json.dumps(
                {
                    "data": data,
                    "duration_ms": duration_ms,
                    "resolved_seed": actual_seed,
                }
            ),
        )
        self._last_model_name = current_model

        return JobResult(data=data, duration_ms=duration_ms, resolved_seed=actual_seed)
