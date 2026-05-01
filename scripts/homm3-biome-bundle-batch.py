#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, request

_SIZE_PATTERN = re.compile(r"^\d{3,4}x\d{3,4}$")

LANE_OUTPUT_FOLDER = {
    "terrain": "terrain",
    "structures": "structures",
    "misc": "misc",
    "bush": "bush",
    "mushroom": "mushroom",
}

_COMPOSITION_ALLOWED = frozenset({"single_tile", "tall_sprite", "wide_scene", "dense_tile"})


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_").lower()


def _normalize_size(value: str) -> str:
    s = str(value).strip().lower().replace(" ", "")
    if "x" in s:
        parts = s.split("x", 1)
        if len(parts) == 2:
            return f"{parts[0]}x{parts[1]}"
    return s


def _validate_size_token(raw: str, *, entry_id: str) -> str:
    size = _normalize_size(raw)
    if not _SIZE_PATTERN.match(size):
        raise SystemExit(f"entry {entry_id!r}: invalid size {raw!r} (expect WxH e.g. 1024x1536)")
    return size


def effective_sizes_for_entry(entry: dict, pack_default_size: str) -> list[str]:
    """Sizes to generate for one pack entry; defaults to pack-level size."""
    default_tok = _validate_size_token(pack_default_size, entry_id=str(entry.get("id", "?")))
    raw = entry.get("sizes")
    if isinstance(raw, list) and raw:
        out: list[str] = []
        for item in raw:
            tok = str(item).strip()
            if not tok:
                continue
            out.append(_validate_size_token(tok, entry_id=str(entry.get("id", "?"))))
        return out if out else [default_tok]
    return [default_tok]


def entry_semantic_fields(entry: dict) -> dict[str, object]:
    """Optional authoring hints merged into prompt sidecars / manifest."""
    extra: dict[str, object] = {}
    wid = entry.get("footprint_tiles_w")
    if isinstance(wid, int) and wid > 0:
        extra["footprint_tiles_w"] = wid
    elif wid is not None:
        raise SystemExit(f"entry {entry.get('id')!r}: footprint_tiles_w must be positive int")

    hit = entry.get("footprint_tiles_h")
    if isinstance(hit, int) and hit > 0:
        extra["footprint_tiles_h"] = hit
    elif hit is not None:
        raise SystemExit(f"entry {entry.get('id')!r}: footprint_tiles_h must be positive int")

    comp = entry.get("composition")
    if isinstance(comp, str) and comp.strip():
        c = comp.strip()
        if c not in _COMPOSITION_ALLOWED:
            raise SystemExit(
                f"entry {entry.get('id')!r}: composition must be one of {_COMPOSITION_ALLOWED}"
            )
        extra["composition"] = c

    cat = entry.get("category")
    if isinstance(cat, str) and cat.strip():
        extra["category"] = cat.strip()

    return extra


def _load_pack(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Batch pack not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Batch pack root must be object: {path}")
    return data


def _validate_loras_field(raw: object) -> list[dict[str, object]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise SystemExit("Batch pack `loras` must be a list of {name, weight} objects.")
    out: list[dict[str, object]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(f"loras[{idx}] must be an object.")
        name = str(item.get("name", "")).strip()
        if not name:
            raise SystemExit(f"loras[{idx}].name is required.")
        w = item.get("weight", 1.0)
        try:
            weight = float(w)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"loras[{idx}].weight must be a number.") from exc
        out.append({"name": name, "weight": weight})
    return out


def _lane_and_entries(pack: dict) -> tuple[str, list[dict]]:
    if isinstance(pack.get("terrain_entries"), list) and pack["terrain_entries"]:
        return "terrain", list(pack["terrain_entries"])
    if isinstance(pack.get("structure_entries"), list) and pack["structure_entries"]:
        return "structures", list(pack["structure_entries"])
    if isinstance(pack.get("misc_entries"), list) and pack["misc_entries"]:
        return "misc", list(pack["misc_entries"])
    if isinstance(pack.get("bush_entries"), list) and pack["bush_entries"]:
        return "bush", list(pack["bush_entries"])
    if isinstance(pack.get("mushroom_entries"), list) and pack["mushroom_entries"]:
        return "mushroom", list(pack["mushroom_entries"])
    raise SystemExit(
        "Pack must define non-empty terrain_entries, structure_entries, misc_entries, "
        "bush_entries, or mushroom_entries."
    )


def _entry_allowed_for_biome(entry: dict, biome_id: str) -> bool:
    inc = entry.get("biomes_include")
    if isinstance(inc, list) and inc:
        allowed = {str(x).strip() for x in inc if str(x).strip()}
        return biome_id in allowed
    exc = entry.get("biomes_exclude")
    if isinstance(exc, list) and exc:
        banned = {str(x).strip() for x in exc if str(x).strip()}
        return biome_id not in banned
    return True


def _asset_summary(entry: dict, biome_id: str, lane: str) -> str:
    explicit = str(entry.get("asset_summary", "")).strip()
    if explicit:
        return explicit
    subject = str(entry.get("prompt_subject", entry.get("id", ""))).strip()
    eid = str(entry.get("id", "")).strip()
    label = subject or eid
    return f"{lane} asset for biome {biome_id}: {label}"


def _post_binary(
    base_url: str, api_key: str, payload: dict, timeout_s: float
) -> tuple[bytes, str | None]:
    req = request.Request(
        f"{base_url.rstrip('/')}/v1/images/generations/binary",
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    with request.urlopen(req, timeout=timeout_s) as resp:
        content = resp.read()
        return content, resp.headers.get("X-Job-Id")


def _should_retry_without_transparency(http_status: int, body: str) -> bool:
    if http_status != 400:
        return False
    low = body.lower()
    return "extra inputs are not permitted" in low


def _build_prompt_meta(
    *,
    scenario_id: str,
    biome_id: str,
    lane: str,
    entry_id: str,
    seed: int,
    prompt: str,
    negative_prompt: str,
    model: str,
    loras_opt: list[dict[str, object]] | None,
    sampler: str | None,
    scheduler: str | None,
    steps: int,
    cfg: float,
    size: str,
    transparent_background: bool | None,
    pack_path: str,
    job_id: str | None,
    relative_image_path: str,
    asset_summary: str,
    asset_tags: list[str],
    semantic_extra: dict[str, object],
) -> dict[str, object]:
    meta: dict[str, object] = {
        "schema_version": 2,
        "scenario_id": scenario_id,
        "biome_id": biome_id,
        "lane": lane,
        "entry_id": entry_id,
        "seed": seed,
        "asset_summary": asset_summary,
        "asset_tags": asset_tags,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": model,
        "loras": loras_opt if loras_opt else [],
        "steps": steps,
        "cfg": cfg,
        "size": size,
        "transparent_background": transparent_background,
        "pack_path": pack_path,
        "job_id": job_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "relative_image_path": relative_image_path.replace("\\", "/"),
    }
    meta.update(semantic_extra)
    if sampler:
        meta["sampler"] = sampler
    if scheduler:
        meta["scheduler"] = scheduler
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HoMM3-inspired biome bundle batch: terrain, structures, misc, bush, or mushroom lane."
        ),
    )
    parser.add_argument(
        "--pack",
        required=True,
        help=(
            "Pack JSON path (homm3 terrain / structures / misc / bush / mushroom biome pack)."
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8700", help="Service base URL.")
    parser.add_argument(
        "--api-key",
        default="",
        help="Generation API key (required unless --dry-run).",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs/homm3-bundle/pass-001",
        help="Pass root; writes biome/lane folders and asset-manifest.ndjson here.",
    )
    parser.add_argument("--timeout-s", type=float, default=360.0, help="Per-request timeout.")
    parser.add_argument("--model-override", default="", help="Optional override for payload model.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only; write batch-run-log.json without calling API.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip API calls when the output PNG already exists and is non-empty; do not delete "
            "asset-manifest.ndjson at start (append only for new rows). Re-run after an interrupted batch."
        ),
    )
    args = parser.parse_args()

    if not args.dry_run and not str(args.api_key).strip():
        parser.error("--api-key is required unless --dry-run.")

    pack_path = Path(args.pack).resolve()
    pack = _load_pack(pack_path)
    lane, entries = _lane_and_entries(pack)

    pack_lane = str(pack.get("lane", "")).strip()
    if pack_lane and pack_lane != lane:
        raise SystemExit(f"Pack lane field {pack_lane!r} disagrees with entries ({lane}).")

    biomes = list(pack.get("biomes", []))
    if not biomes:
        raise SystemExit("Pack has no biomes[].")

    prompt_template = str(pack.get("prompt_template", "")).strip()
    if "{biome_hint}" not in prompt_template or "{subject}" not in prompt_template:
        raise SystemExit("prompt_template must include {biome_hint} and {subject} placeholders.")

    negative_prompt = str(pack.get("negative_prompt", ""))
    model = args.model_override.strip() or str(pack.get("model", "")).strip()
    if not model:
        raise SystemExit("Pack model is empty and --model-override not set.")

    pack_default_size = str(pack.get("size", "1024x1024"))
    pack_default_size = _validate_size_token(pack_default_size, entry_id="__pack__")

    steps = int(pack.get("steps", 28))
    cfg = float(pack.get("cfg", 1.0))
    default_transparent = pack.get("transparent_background")
    seeds_raw = pack.get("seeds", [101])
    seeds = [int(s) for s in seeds_raw]

    sampler_opt = pack.get("sampler")
    scheduler_opt = pack.get("scheduler")
    sampler_str = str(sampler_opt).strip() if sampler_opt is not None else ""
    scheduler_str = str(scheduler_opt).strip() if scheduler_opt is not None else ""

    loras_opt = _validate_loras_field(pack.get("loras"))

    try:
        lane_folder = LANE_OUTPUT_FOLDER[lane]
    except KeyError as exc:
        raise SystemExit(f"Unsupported lane for output folder: {lane!r}") from exc
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    manifest_path = out_root / "asset-manifest.ndjson"
    if not args.dry_run and manifest_path.exists() and not args.resume:
        manifest_path.unlink()

    run_log: dict[str, object] = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            "base_url": args.base_url,
            "model": model,
            "pack": str(pack_path.as_posix()),
            "lane": lane,
            "dry_run": args.dry_run,
            "sampler": sampler_str or None,
            "scheduler": scheduler_str or None,
            "loras": loras_opt,
            "pack_default_size": pack_default_size,
            "resume": args.resume,
        },
        "runs": [],
    }

    total = 0
    failures = 0
    skipped_existing = 0

    def append_manifest(record: dict[str, object]) -> None:
        if args.dry_run:
            return
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    for biome_raw in biomes:
        if not isinstance(biome_raw, dict):
            print("Skipping invalid biome entry.", file=sys.stderr)
            continue
        biome_id = str(biome_raw.get("id", "")).strip()
        biome_hint = str(biome_raw.get("hint", "")).strip()
        if not biome_id or not biome_hint:
            print("Skipping biome missing id/hint.", file=sys.stderr)
            continue

        for entry_raw in entries:
            if not isinstance(entry_raw, dict):
                continue
            entry_id = str(entry_raw.get("id", "")).strip()
            subject = str(entry_raw.get("prompt_subject", "")).strip()
            if not entry_id or not subject:
                print(f"Skipping entry missing id/prompt_subject: {entry_raw}", file=sys.stderr)
                continue

            if not _entry_allowed_for_biome(entry_raw, biome_id):
                continue

            safe_entry = _safe(entry_id)
            semantic_extra = entry_semantic_fields(entry_raw)
            sizes_list = effective_sizes_for_entry(entry_raw, pack_default_size)

            tb_entry = entry_raw.get("transparent_background")
            if tb_entry is None:
                if isinstance(default_transparent, bool):
                    transparent_background = bool(default_transparent)
                else:
                    transparent_background = None
            else:
                transparent_background = bool(tb_entry)

            summary = _asset_summary(entry_raw, biome_id, lane)
            tags_raw = entry_raw.get("asset_tags", [])
            asset_tags = (
                [str(t).strip() for t in tags_raw if str(t).strip()]
                if isinstance(tags_raw, list)
                else []
            )

            prompt = prompt_template.format(biome_hint=biome_hint, subject=subject)

            for size in sizes_list:
                scenario_core = f"{biome_id}__{lane}__{entry_id}"
                scenario_id = f"{scenario_core}__{size}"

                for seed in seeds:
                    total += 1
                    payload: dict[str, object] = {
                        "model": model,
                        "prompt": prompt,
                        "negative_prompt": negative_prompt,
                        "size": size,
                        "steps": steps,
                        "cfg": cfg,
                        "seed": seed,
                    }
                    if transparent_background is not None:
                        payload["transparent_background"] = transparent_background
                    if sampler_str:
                        payload["sampler"] = sampler_str
                    if scheduler_str:
                        payload["scheduler"] = scheduler_str
                    if loras_opt:
                        payload["loras"] = loras_opt

                    safe_size_token = _safe(size.replace("x", "_"))
                    target = (
                        out_root
                        / _safe(biome_id)
                        / lane_folder
                        / f"{safe_entry}__{safe_size_token}__s{seed}.png"
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    rel_img = target.relative_to(out_root).as_posix()

                    base_record_kwargs = dict(
                        scenario_id=scenario_id,
                        biome_id=biome_id,
                        lane=lane,
                        entry_id=entry_id,
                        seed=seed,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        model=model,
                        loras_opt=loras_opt,
                        sampler=sampler_str or None,
                        scheduler=scheduler_str or None,
                        steps=steps,
                        cfg=cfg,
                        size=size,
                        transparent_background=transparent_background,
                        pack_path=str(pack_path.as_posix()),
                        relative_image_path=rel_img,
                        asset_summary=summary,
                        asset_tags=asset_tags,
                        semantic_extra=semantic_extra,
                    )

                    if args.dry_run:
                        print(f"[DRY] {scenario_id} seed={seed} -> {target.as_posix()}")
                        row = {
                            "scenario_id": scenario_id,
                            "biome_id": biome_id,
                            "entry_id": entry_id,
                            "seed": seed,
                            "size": size,
                            "status": "dry_run",
                            "payload": payload,
                            "asset_summary": summary,
                            "semantic": semantic_extra,
                            "output_path": str(target.as_posix()),
                        }
                        run_log["runs"].append(row)
                        continue

                    if (
                        args.resume
                        and target.is_file()
                        and target.stat().st_size > 0
                    ):
                        skipped_existing += 1
                        sidecar_path = target.with_suffix(".prompt.json")
                        print(
                            f"[SKIP] {scenario_id} seed={seed} existing -> {target.as_posix()}",
                            flush=True,
                        )
                        run_log["runs"].append(
                            {
                                "scenario_id": scenario_id,
                                "biome_id": biome_id,
                                "entry_id": entry_id,
                                "seed": seed,
                                "size": size,
                                "status": "skipped_existing",
                                "output_path": str(target.as_posix()),
                                "prompt_sidecar": str(sidecar_path.as_posix())
                                if sidecar_path.is_file()
                                else None,
                            }
                        )
                        continue

                    try:
                        png, job_id = _post_binary(
                            args.base_url, args.api_key, payload, args.timeout_s
                        )
                        target.write_bytes(png)
                        print(f"[OK] {scenario_id} seed={seed} -> {target.as_posix()}")

                        meta = _build_prompt_meta(job_id=job_id, **base_record_kwargs)
                        sidecar = target.with_suffix(".prompt.json")
                        sidecar.write_text(
                            json.dumps(meta, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        append_manifest(meta)

                        run_log["runs"].append(
                            {
                                "scenario_id": scenario_id,
                                "biome_id": biome_id,
                                "entry_id": entry_id,
                                "seed": seed,
                                "size": size,
                                "status": "ok",
                                "job_id": job_id,
                                "output_path": str(target.as_posix()),
                                "prompt_sidecar": str(sidecar.as_posix()),
                            }
                        )
                    except error.HTTPError as exc:
                        body = exc.read().decode("utf-8", errors="replace")
                        intended = _build_prompt_meta(job_id=None, **base_record_kwargs)
                        if _should_retry_without_transparency(exc.code, body):
                            retry_payload = dict(payload)
                            retry_payload.pop("transparent_background", None)
                            retry_tb = None
                            try:
                                png, job_id = _post_binary(
                                    args.base_url, args.api_key, retry_payload, args.timeout_s
                                )
                                target.write_bytes(png)
                                print(
                                    f"[OK] {scenario_id} seed={seed} -> {target.as_posix()} "
                                    "(fallback: no transparent_background field)"
                                )
                                kw_no_tb = {
                                    k: v
                                    for k, v in base_record_kwargs.items()
                                    if k != "transparent_background"
                                }
                                meta = _build_prompt_meta(
                                    job_id=job_id,
                                    transparent_background=retry_tb,
                                    **kw_no_tb,
                                )
                                meta["transparent_background_fallback"] = True
                                meta["original_transparent_background"] = transparent_background
                                sidecar = target.with_suffix(".prompt.json")
                                sidecar.write_text(
                                    json.dumps(meta, indent=2) + "\n",
                                    encoding="utf-8",
                                )
                                append_manifest(meta)
                                run_log["runs"].append(
                                    {
                                        "scenario_id": scenario_id,
                                        "biome_id": biome_id,
                                        "entry_id": entry_id,
                                        "seed": seed,
                                        "size": size,
                                        "status": "ok_fallback_without_transparency_field",
                                        "job_id": job_id,
                                        "output_path": str(target.as_posix()),
                                        "prompt_sidecar": str(sidecar.as_posix()),
                                        "retry_payload": retry_payload,
                                    }
                                )
                                continue
                            except error.HTTPError as retry_exc:
                                body = retry_exc.read().decode("utf-8", errors="replace")
                                exc = retry_exc

                        failures += 1
                        print(
                            f"[FAIL] {scenario_id} seed={seed} status={exc.code} body={body}",
                            file=sys.stderr,
                        )
                        run_log["runs"].append(
                            {
                                "scenario_id": scenario_id,
                                "biome_id": biome_id,
                                "entry_id": entry_id,
                                "seed": seed,
                                "size": size,
                                "status": "http_error",
                                "http_status": exc.code,
                                "error_body": body,
                                "intended_prompt": prompt,
                                "intended_negative_prompt": negative_prompt,
                                "payload": payload,
                                "asset_prompt_meta": intended,
                            }
                        )
                    except Exception as exc:
                        failures += 1
                        intended = _build_prompt_meta(job_id=None, **base_record_kwargs)
                        print(f"[FAIL] {scenario_id} seed={seed} error={exc}", file=sys.stderr)
                        run_log["runs"].append(
                            {
                                "scenario_id": scenario_id,
                                "biome_id": biome_id,
                                "entry_id": entry_id,
                                "seed": seed,
                                "size": size,
                                "status": "error",
                                "error": str(exc),
                                "intended_prompt": prompt,
                                "payload": payload,
                                "asset_prompt_meta": intended,
                            }
                        )

    log_path = out_root / "batch-run-log.json"
    log_path.write_text(json.dumps(run_log, indent=2) + "\n", encoding="utf-8")
    print(f"Run log: {log_path.as_posix()}")
    if not args.dry_run:
        print(f"Manifest: {manifest_path.as_posix()}")
    print(f"Total planned/completed iterations: {total}")
    print(f"Skipped existing (resume): {skipped_existing}")
    print(f"Failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
