from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.registry.models import Registry, load_registry


@dataclass(frozen=True, slots=True)
class StartupContext:
    imagegen_env: str
    models_yaml_path: Path
    models_root: Path
    workflows_root: Path
    vram_budget_gb: float
    comfyui_url: str
    webhook_allowed_hosts: str
    webhook_allow_any_host: bool
    webhook_signing_secrets: str


class StartupCheckError(RuntimeError):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


def build_context_from_env() -> StartupContext:
    return StartupContext(
        imagegen_env=os.environ.get("IMAGEGEN_ENV", "dev").lower(),
        models_yaml_path=Path(os.environ.get("MODELS_YAML_PATH", "config/models.yaml")),
        models_root=Path(os.environ.get("MODELS_ROOT", "./models")),
        workflows_root=Path(os.environ.get("WORKFLOWS_ROOT", ".")),
        vram_budget_gb=float(os.environ.get("VRAM_BUDGET_GB", "12")),
        comfyui_url=os.environ.get("COMFYUI_URL", "http://comfyui:8188"),
        webhook_allowed_hosts=os.environ.get("WEBHOOK_ALLOWED_HOSTS", ""),
        webhook_allow_any_host=os.environ.get("WEBHOOK_ALLOW_ANY_HOST", "false").lower() == "true",
        webhook_signing_secrets=os.environ.get("WEBHOOK_SIGNING_SECRETS", ""),
    )


def run_startup_checks(ctx: StartupContext) -> Registry:
    _check_prod_webhook_posture(ctx)
    _check_comfyui_endpoint_not_public(ctx)
    return _load_registry_checked(ctx)


def _check_prod_webhook_posture(ctx: StartupContext) -> None:
    if ctx.imagegen_env != "prod":
        return

    allowed_hosts = [h.strip() for h in ctx.webhook_allowed_hosts.split(",") if h.strip()]
    if not allowed_hosts:
        raise StartupCheckError(
            "webhook_allowed_hosts_empty",
            "WEBHOOK_ALLOWED_HOSTS must be non-empty in prod",
        )
    if ctx.webhook_allow_any_host:
        raise StartupCheckError(
            "webhook_allow_any_host_forbidden",
            "WEBHOOK_ALLOW_ANY_HOST=true is forbidden in prod",
        )
    if not ctx.webhook_signing_secrets.strip():
        raise StartupCheckError(
            "webhook_signing_secrets_empty",
            "WEBHOOK_SIGNING_SECRETS must be non-empty in prod",
        )


def _check_comfyui_endpoint_not_public(ctx: StartupContext) -> None:
    parsed = urlparse(ctx.comfyui_url)
    host = parsed.hostname
    if not host:
        raise StartupCheckError(
            "comfyui_url_invalid",
            f"COMFYUI_URL has no hostname: {ctx.comfyui_url!r}",
        )

    try:
        infos = socket.getaddrinfo(host, parsed.port)
    except OSError as exc:
        raise StartupCheckError(
            "comfyui_dns_failed",
            f"cannot resolve host {host!r}: {exc}",
        ) from exc
    if not infos:
        raise StartupCheckError("comfyui_dns_empty", f"no DNS answers for host {host!r}")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise StartupCheckError("comfyui_ip_invalid", f"invalid IP {ip_str!r}") from exc
        if ip.is_global:
            raise StartupCheckError(
                "comfyui_public_ip_forbidden",
                f"COMFYUI_URL resolves to public IP {ip_str}",
            )


def _load_registry_checked(ctx: StartupContext) -> Registry:
    try:
        return load_registry(
            yaml_path=ctx.models_yaml_path,
            models_root=ctx.models_root,
            workflows_root=ctx.workflows_root,
            vram_budget_gb=ctx.vram_budget_gb,
        )
    except Exception as exc:
        raise StartupCheckError("registry_validation_failed", str(exc)) from exc
