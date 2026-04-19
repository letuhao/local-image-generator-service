from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

log = structlog.get_logger()

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_/\-.]*$")
_SAFETENSORS_SUFFIX = ".safetensors"
# Sidecar JSON is tiny (metadata only). Cap the read at 1 MiB so a crafted or
# accidentally-huge file can't blow memory or block the scan.
_SIDECAR_MAX_BYTES = 1 * 1024 * 1024

SidecarStatus = Literal["ok", "missing", "malformed", "oversized"]


@dataclass(frozen=True, slots=True)
class LoraMeta:
    name: str
    filename: str
    sha256: str | None
    source: Literal["civitai", "local"]
    civitai_model_id: int | None
    civitai_version_id: int | None
    base_model_hint: str | None
    trigger_words: tuple[str, ...]
    fetched_at: str | None
    size_bytes: int
    addressable: bool
    reason: str | None
    sidecar_status: SidecarStatus
    last_used: str | None  # ISO-8601, updated by validation.resolve_and_validate


def _unaddressable_reason(name: str) -> str | None:
    if not name:
        return "name is empty"
    if not _NAME_RE.match(name):
        return (
            "name contains disallowed characters "
            "(allowed: [A-Za-z0-9_/-.] after a leading alphanumeric/underscore)"
        )
    return None


def _read_sidecar(sidecar_path: Path) -> tuple[dict | None, SidecarStatus]:
    """Return (parsed_dict_or_None, status).

    Status values:
      - "missing": sidecar file does not exist
      - "oversized": sidecar exists but exceeds _SIDECAR_MAX_BYTES
      - "malformed": sidecar exists, readable, but bad JSON or not-a-dict
      - "ok": parsed successfully
    """
    if not sidecar_path.is_file():
        return None, "missing"
    try:
        stat = sidecar_path.stat()
    except OSError as exc:
        log.warning("lora.scan.sidecar_stat_failed", sidecar=str(sidecar_path), error=str(exc))
        return None, "malformed"
    if stat.st_size > _SIDECAR_MAX_BYTES:
        log.warning(
            "lora.scan.sidecar_oversized",
            sidecar=str(sidecar_path),
            size_bytes=stat.st_size,
            max_bytes=_SIDECAR_MAX_BYTES,
        )
        return None, "oversized"
    try:
        with sidecar_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "lora.scan.sidecar_malformed",
            sidecar=str(sidecar_path),
            error=str(exc),
        )
        return None, "malformed"
    if not isinstance(data, dict):
        log.warning("lora.scan.sidecar_not_object", sidecar=str(sidecar_path))
        return None, "malformed"
    return data, "ok"


def _meta_from_sidecar(
    name: str,
    filename: str,
    size_bytes: int,
    sidecar: dict | None,
    addressable: bool,
    reason: str | None,
    sidecar_status: SidecarStatus,
) -> LoraMeta:
    if sidecar is None:
        return LoraMeta(
            name=name,
            filename=filename,
            sha256=None,
            source="local",
            civitai_model_id=None,
            civitai_version_id=None,
            base_model_hint=None,
            trigger_words=(),
            fetched_at=None,
            size_bytes=size_bytes,
            addressable=addressable,
            reason=reason,
            sidecar_status=sidecar_status,
            last_used=None,
        )
    trigger_raw = sidecar.get("trigger_words")
    if isinstance(trigger_raw, list):
        triggers = tuple(str(t) for t in trigger_raw if isinstance(t, str))
    else:
        triggers = ()
    source = sidecar.get("source")
    if source == "civitai":
        source_literal: Literal["civitai", "local"] = "civitai"
    elif source in (None, "local"):
        source_literal = "local"
    else:
        # Unknown source label — coerce to "local" but warn so Cycle 6+ can
        # catch writer regressions.
        log.warning(
            "lora.scan.unknown_source_label",
            sidecar_source=source,
            name=name,
        )
        source_literal = "local"
    return LoraMeta(
        name=name,
        filename=filename,
        sha256=sidecar.get("sha256") if isinstance(sidecar.get("sha256"), str) else None,
        source=source_literal,
        civitai_model_id=(
            sidecar.get("civitai_model_id")
            if isinstance(sidecar.get("civitai_model_id"), int)
            else None
        ),
        civitai_version_id=(
            sidecar.get("civitai_version_id")
            if isinstance(sidecar.get("civitai_version_id"), int)
            else None
        ),
        base_model_hint=(
            sidecar.get("base_model_hint")
            if isinstance(sidecar.get("base_model_hint"), str)
            else None
        ),
        trigger_words=triggers,
        fetched_at=(
            sidecar.get("fetched_at") if isinstance(sidecar.get("fetched_at"), str) else None
        ),
        size_bytes=size_bytes,
        addressable=addressable,
        reason=reason,
        sidecar_status=sidecar_status,
        last_used=(sidecar.get("last_used") if isinstance(sidecar.get("last_used"), str) else None),
    )


def scan_loras(root: Path) -> list[LoraMeta]:
    """Walk `root` recursively; return LoraMeta list sorted by name.

    - Non-.safetensors files ignored (.crdownload, .part, .json, anything else).
    - Entries whose resolved path escapes `root` (via directory symlinks that
      Python's Path.rglob follows on ≥3.12) are skipped so `GET /v1/loras`
      never lists filenames outside the intended root.
    - Sidecar `<stem>.json` loaded alongside each .safetensors; missing,
      oversized, or malformed sidecars surface via `sidecar_status`.
    - Filenames that fail the request-regex are still returned with
      addressable=False + a reason string; callers referencing such names are
      rejected at validation.
    """
    root = root.resolve()
    if not root.is_dir():
        log.info("lora.scan.root_missing", root=str(root))
        return []

    results: list[LoraMeta] = []
    skipped_outside = 0
    for path in sorted(root.rglob("*" + _SAFETENSORS_SUFFIX)):
        if not path.is_file():
            continue
        # Defense against directory symlinks / junctions that escape the root —
        # Python's rglob follows them by default.
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            skipped_outside += 1
            log.warning("lora.scan.skipped_outside_root", path=str(path))
            continue
        relative = path.relative_to(root)
        posix_name = relative.with_suffix("").as_posix()
        filename = relative.as_posix()
        try:
            size_bytes = path.stat().st_size
        except OSError as exc:
            log.warning("lora.scan.stat_failed", path=str(path), error=str(exc))
            continue

        reason = _unaddressable_reason(posix_name)
        addressable = reason is None
        sidecar_path = path.with_suffix(".json")
        sidecar, sidecar_status = _read_sidecar(sidecar_path)
        if sidecar_status == "missing":
            # Per-file hint goes to DEBUG — the summary at the end is the INFO
            # line we want. Emitting per-file at INFO floods logs on user dirs
            # with hundreds of sidecar-less entries.
            log.debug(
                "lora.scan.missing_sidecar",
                name=posix_name,
                filename=filename,
            )
        results.append(
            _meta_from_sidecar(
                name=posix_name,
                filename=filename,
                size_bytes=size_bytes,
                sidecar=sidecar,
                addressable=addressable,
                reason=reason,
                sidecar_status=sidecar_status,
            )
        )
    log.info(
        "lora.scan.complete",
        root=str(root),
        total=len(results),
        addressable=sum(1 for m in results if m.addressable),
        skipped_outside_root=skipped_outside,
    )
    return results
