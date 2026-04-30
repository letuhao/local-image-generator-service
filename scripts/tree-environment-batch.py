#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


DEFAULT_PROMPT_TEMPLATE = (
    "sprite object, single {tree_type}, top-down degree view, shallow root, "
    "isolated object, single centered object, white background, white ground, "
    "hand-painted fantasy strategy game style, HoMM3 art style, {environment_hint}"
)

DEFAULT_NEGATIVE = (
    "terrain tile, grass patch, soil texture, dirt floor, platform, pedestal, island, cliff, "
    "rocks, scene background, multiple objects, dark ground, colored ground, "
    "person, human, character, face"
)

FORBIDDEN_HINT_TOKENS = {
    "ground",
    "grass",
    "soil",
    "dirt",
    "terrain",
    "floor",
    "roots",
    "base",
    "platform",
    "shadow",
}


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_").lower()


def _validate_hint(env_id: str, hint: str) -> None:
    hint_words = set(re.findall(r"\b\w+\b", hint.lower()))
    conflicts = sorted(hint_words & FORBIDDEN_HINT_TOKENS)
    if conflicts:
        raise SystemExit(
            f"Environment {env_id!r} hint contains forbidden tokens: {conflicts}. "
            f"Hint: {hint!r}"
        )


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


def _post_binary(base_url: str, api_key: str, payload: dict, timeout_s: float) -> tuple[bytes, str | None]:
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate tree sprite batches across environment variants."
    )
    parser.add_argument(
        "--pack",
        default="docs/architecture/tree-environment-batch-pack.json",
        help="Batch pack JSON describing environment variants.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8700", help="Service base URL.")
    parser.add_argument("--api-key", required=True, help="Generation API key.")
    parser.add_argument(
        "--model-override",
        default="",
        help="Optional override for payload model (ignores pack model).",
    )
    parser.add_argument(
        "--prompt-template-override",
        default="",
        help="Optional override for prompt template (must contain {environment_hint} and {tree_type}).",
    )
    parser.add_argument(
        "--negative-prompt-override",
        default="",
        help="Optional override for negative prompt.",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs/tree-review/pass-001",
        help="Output root for generated PNGs.",
    )
    parser.add_argument("--timeout-s", type=float, default=360.0, help="Per-request timeout.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payload plan only; do not call API.",
    )
    args = parser.parse_args()

    pack = _load_pack(Path(args.pack))
    model = args.model_override.strip() or str(pack.get("model", "terrain-realisticfantasy-v30"))
    size = str(pack.get("size", "1024x1024"))
    steps = int(pack.get("steps", 30))
    cfg = float(pack.get("cfg", 7.5))
    transparent_background = bool(pack.get("transparent_background", True))
    seeds = list(pack.get("seeds", [102]))
    tree_types = list(pack.get("tree_types", ["ancient oak tree"]))
    prompt_template = args.prompt_template_override.strip() or str(
        pack.get("prompt_template", DEFAULT_PROMPT_TEMPLATE)
    )
    negative_prompt = args.negative_prompt_override.strip() or str(
        pack.get("negative_prompt", DEFAULT_NEGATIVE)
    )
    environments = list(pack.get("environments", []))
    if not environments:
        raise SystemExit("Batch pack has no environments.")
    if not tree_types:
        raise SystemExit("Batch pack has no tree_types.")

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    run_log: dict[str, object] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "model": model,
            "size": size,
            "steps": steps,
            "cfg": cfg,
            "pack": args.pack,
        },
        "runs": [],
    }

    total = 0
    failures = 0
    for env in environments:
        if not isinstance(env, dict):
            print("Skipping invalid environment entry (not object).", file=sys.stderr)
            continue
        env_id = str(env.get("id", "")).strip()
        env_hint = str(env.get("hint", "")).strip()
        if not env_id or not env_hint:
            print("Skipping invalid environment entry (missing id/hint).", file=sys.stderr)
            continue
        _validate_hint(env_id, env_hint)
        safe_env = _safe(env_id)
        for tree_type_raw in tree_types:
            tree_type = str(tree_type_raw).strip()
            if not tree_type:
                print(f"Skipping blank tree_type in env={env_id}.", file=sys.stderr)
                continue
            safe_tree = _safe(tree_type)
            for seed_raw in seeds:
                seed = int(seed_raw)
                prompt = prompt_template.format(environment_hint=env_hint, tree_type=tree_type)
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "size": size,
                    "steps": steps,
                    "cfg": cfg,
                    "seed": seed,
                    "transparent_background": transparent_background,
                }
                total += 1
                filename = f"{safe_tree}__s{seed}.png"
                target = out_root / safe_env / filename
                target.parent.mkdir(parents=True, exist_ok=True)

                if args.dry_run:
                    print(f"[DRY] {safe_env}/{safe_tree} seed={seed}")
                    run_log["runs"].append(
                        {
                            "environment_id": env_id,
                            "tree_type": tree_type,
                            "seed": seed,
                            "status": "dry_run",
                            "payload": payload,
                            "output_path": str(target.as_posix()),
                        }
                    )
                    continue

                try:
                    png, job_id = _post_binary(args.base_url, args.api_key, payload, args.timeout_s)
                    target.write_bytes(png)
                    print(f"[OK] {safe_env}/{safe_tree} seed={seed} -> {target.as_posix()}")
                    run_log["runs"].append(
                        {
                            "environment_id": env_id,
                            "tree_type": tree_type,
                            "seed": seed,
                            "status": "ok",
                            "job_id": job_id,
                            "output_path": str(target.as_posix()),
                        }
                    )
                except error.HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="replace")
                    if _should_retry_without_transparency(exc.code, body):
                        retry_payload = dict(payload)
                        retry_payload.pop("transparent_background", None)
                        try:
                            png, job_id = _post_binary(
                                args.base_url, args.api_key, retry_payload, args.timeout_s
                            )
                            target.write_bytes(png)
                            print(
                                f"[OK] {safe_env}/{safe_tree} seed={seed} -> {target.as_posix()} "
                                "(fallback: no transparent_background field)"
                            )
                            run_log["runs"].append(
                                {
                                    "environment_id": env_id,
                                    "tree_type": tree_type,
                                    "seed": seed,
                                    "status": "ok_fallback_without_transparency_field",
                                    "job_id": job_id,
                                    "output_path": str(target.as_posix()),
                                }
                            )
                            continue
                        except error.HTTPError as retry_exc:
                            body = retry_exc.read().decode("utf-8", errors="replace")
                            exc = retry_exc
                    failures += 1
                    print(
                        f"[FAIL] {safe_env}/{safe_tree} seed={seed} status={exc.code} body={body}",
                        file=sys.stderr,
                    )
                    run_log["runs"].append(
                        {
                            "environment_id": env_id,
                            "tree_type": tree_type,
                            "seed": seed,
                            "status": "http_error",
                            "http_status": exc.code,
                            "error_body": body,
                        }
                    )
                except Exception as exc:  # pragma: no cover - defensive runtime guard
                    failures += 1
                    print(f"[FAIL] {safe_env}/{safe_tree} seed={seed} error={exc}", file=sys.stderr)
                    run_log["runs"].append(
                        {
                            "environment_id": env_id,
                            "tree_type": tree_type,
                            "seed": seed,
                            "status": "error",
                            "error": str(exc),
                        }
                    )

    log_path = out_root / "batch-run-log.json"
    log_path.write_text(json.dumps(run_log, indent=2) + "\n", encoding="utf-8")
    print(f"Run log: {log_path.as_posix()}")
    print(f"Total planned: {total}")
    print(f"Failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
