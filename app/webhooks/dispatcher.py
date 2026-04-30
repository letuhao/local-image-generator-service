from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import structlog

from app.queue.jobs import (
    Job,
    complete_attempt,
    create_attempt,
    get_latest_attempt,
    scan_webhook_candidates,
    set_attempt_delivering,
    set_webhook_delivery_status,
)
from app.queue.store import JobStore
from app.webhooks.retry import next_retry_at
from app.webhooks.signing import build_signature_header, sign_payload

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WebhookDispatcherConfig:
    max_in_flight: int
    http_timeout_s: float

    @classmethod
    def from_env(cls) -> "WebhookDispatcherConfig":
        return cls(
            max_in_flight=max(1, int(os.environ.get("WEBHOOK_MAX_IN_FLIGHT", "8"))),
            http_timeout_s=max(1.0, float(os.environ.get("WEBHOOK_HTTP_TIMEOUT_S", "10"))),
        )


class WebhookDispatcher:
    def __init__(self, *, store: JobStore, http_client: httpx.AsyncClient) -> None:
        self._store = store
        self._http = http_client
        self._cfg = WebhookDispatcherConfig.from_env()
        self._sema = asyncio.Semaphore(self._cfg.max_in_flight)

    async def run(self) -> None:
        log.info("webhook_dispatcher.started", max_in_flight=self._cfg.max_in_flight)
        try:
            while True:
                try:
                    await self._tick()
                except Exception as exc:  # pragma: no cover - defensive loop guard
                    log.exception("webhook_dispatcher.tick_failed", error=str(exc))
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            log.info("webhook_dispatcher.cancelled")
            raise

    async def _tick(self) -> None:
        jobs = await scan_webhook_candidates(self._store)
        if not jobs:
            return
        tasks = [self._process_one(job) for job in jobs]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_one(self, job: Job) -> None:
        async with self._sema:
            if job.webhook_url is None:
                return
            if job.mode == "sync" and job.response_delivered:
                await set_webhook_delivery_status(self._store, job.id, "suppressed")
                return

            latest = await get_latest_attempt(self._store, job.id)
            now = datetime.now(UTC)
            if latest is not None:
                if latest.status in {"pending", "delivering"}:
                    if latest.next_retry_at is None:
                        await complete_attempt(
                            self._store,
                            attempt_id=latest.id,
                            status="failed",
                            status_code=latest.status_code,
                            response_body_snippet=latest.response_body_snippet,
                            error=latest.error,
                            error_code=latest.error_code or "webhook_dispatcher_restart",
                            next_retry_at=None,
                        )
                    else:
                        due = datetime.fromisoformat(latest.next_retry_at)
                        if due > now:
                            return
                elif latest.status == "succeeded":
                    await set_webhook_delivery_status(self._store, job.id, "succeeded")
                    return
                elif latest.status == "failed" and latest.next_retry_at:
                    due = datetime.fromisoformat(latest.next_retry_at)
                    if due > now:
                        return

            attempt_n = 1 if latest is None else latest.attempt_n + 1
            attempt = await create_attempt(
                self._store,
                job_id=job.id,
                attempt_n=attempt_n,
                next_retry_at=None,
            )
            await set_attempt_delivering(self._store, attempt.id)

            if not self._toctou_allows(job.webhook_url):
                await complete_attempt(
                    self._store,
                    attempt_id=attempt.id,
                    status="failed",
                    status_code=None,
                    response_body_snippet=None,
                    error=None,
                    error_code="webhook_ssrf_blocked",
                    next_retry_at=None,
                )
                await set_webhook_delivery_status(self._store, job.id, "failed")
                return

            secret = self._first_signing_secret()
            if not secret:
                await complete_attempt(
                    self._store,
                    attempt_id=attempt.id,
                    status="failed",
                    status_code=None,
                    response_body_snippet=None,
                    error=None,
                    error_code="webhook_signing_error",
                    next_retry_at=None,
                )
                await set_webhook_delivery_status(self._store, job.id, "failed")
                log.warning("webhook.signing_secret_missing", job_id=job.id)
                return

            event, body = self._build_payload(job)
            ts = int(time.time())
            sig = sign_payload(ts, body, secret)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "image-gen-service/0.1",
                "X-ImageGen-Event": event,
                "X-ImageGen-Job-Id": job.id,
                "X-ImageGen-Delivery-Id": str(uuid.uuid4()),
                "X-ImageGen-Timestamp": str(ts),
                "X-ImageGen-Signature": build_signature_header(ts, sig),
            }
            for k, v in (job.webhook_headers or {}).items():
                lk = k.lower()
                if lk.startswith("x-imagegen-") or lk in {
                    "content-type",
                    "host",
                    "authorization",
                    "user-agent",
                }:
                    continue
                headers[k] = v

            try:
                resp = await self._http.post(
                    job.webhook_url,
                    content=body,
                    headers=headers,
                    timeout=self._cfg.http_timeout_s,
                    follow_redirects=False,
                )
            except httpx.HTTPError as exc:
                retry_at = next_retry_at(attempt_n)
                await complete_attempt(
                    self._store,
                    attempt_id=attempt.id,
                    status="failed",
                    status_code=None,
                    response_body_snippet=None,
                    error=str(exc),
                    error_code=None,
                    next_retry_at=retry_at,
                )
                await set_webhook_delivery_status(
                    self._store, job.id, "pending" if retry_at else "failed"
                )
                log.warning(
                    "webhook.delivery_http_error",
                    job_id=job.id,
                    error=str(exc),
                    retry=bool(retry_at),
                )
                return

            if 200 <= resp.status_code < 300:
                await complete_attempt(
                    self._store,
                    attempt_id=attempt.id,
                    status="succeeded",
                    status_code=resp.status_code,
                    response_body_snippet=(resp.text or "")[:256],
                    error=None,
                    error_code=None,
                    next_retry_at=None,
                )
                await set_webhook_delivery_status(self._store, job.id, "succeeded")
                log.info("webhook.delivery_succeeded", job_id=job.id, status_code=resp.status_code)
                return

            retryable = resp.status_code == 429 or 500 <= resp.status_code <= 599
            retry_at = next_retry_at(attempt_n) if retryable else None
            await complete_attempt(
                self._store,
                attempt_id=attempt.id,
                status="failed",
                status_code=resp.status_code,
                response_body_snippet=(resp.text or "")[:256],
                error=None,
                error_code="webhook_redirect" if 300 <= resp.status_code <= 399 else None,
                next_retry_at=retry_at,
            )
            await set_webhook_delivery_status(
                self._store, job.id, "pending" if retry_at else "failed"
            )
            log.warning(
                "webhook.delivery_failed",
                job_id=job.id,
                status_code=resp.status_code,
                retry=bool(retry_at),
            )

    @staticmethod
    def _build_payload(job: Job) -> tuple[str, bytes]:
        if job.status == "completed":
            event = "job.completed"
            parsed = json.loads(job.result_json) if job.result_json else {}
            payload = {
                "event": event,
                "job": {
                    "id": job.id,
                    "status": job.status,
                    "created": int(time.time()),
                    "data": parsed.get("data", []),
                },
            }
        else:
            event = f"job.{job.status}"
            payload = {
                "event": event,
                "job": {
                    "id": job.id,
                    "status": job.status,
                    "error": {
                        "code": job.error_code or job.status,
                        "message": job.error_message or "",
                    },
                },
            }
        return event, json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _first_signing_secret() -> str | None:
        raw = os.environ.get("WEBHOOK_SIGNING_SECRETS", "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts[0] if parts else None

    @staticmethod
    def _toctou_allows(url: str) -> bool:
        parsed = urlparse(url)
        imagegen_env = os.environ.get("IMAGEGEN_ENV", "dev").lower()
        if imagegen_env == "prod" and parsed.scheme != "https":
            return False
        if imagegen_env != "prod" and parsed.scheme not in {"http", "https"}:
            return False

        allow_any = os.environ.get("WEBHOOK_ALLOW_ANY_HOST", "false").lower() == "true"
        if allow_any and imagegen_env == "dev":
            return True

        allowed_hosts = [
            h.strip().lower()
            for h in os.environ.get("WEBHOOK_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]
        host = (parsed.hostname or "").lower()
        if not allowed_hosts:
            return False
        if host not in allowed_hosts:
            return False

        allow_private = os.environ.get("WEBHOOK_ALLOW_PRIVATE", "false").lower() == "true"
        if allow_private and imagegen_env == "dev":
            return True
        if not host:
            return False
        return WebhookDispatcher._host_resolves_public(host)

    @staticmethod
    def _host_resolves_public(host: str) -> bool:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        if not infos:
            return False
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        return True

