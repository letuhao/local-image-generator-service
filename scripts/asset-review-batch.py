#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_").lower()


def _load_pack(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Batch pack not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


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
        return resp.read(), resp.headers.get("X-Job-Id")


def _should_retry_without_transparency(http_status: int, body: str) -> bool:
    return http_status == 400 and "extra inputs are not permitted" in body.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate broad asset-type review matrix.")
    parser.add_argument("--pack", default="docs/architecture/flux-asset-review-pack.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8700")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--out-dir", default="outputs/asset-review/pass-001")
    parser.add_argument("--timeout-s", type=float, default=360.0)
    args = parser.parse_args()

    pack = _load_pack(Path(args.pack))
    model = str(pack["model"])
    negative_prompt = str(pack.get("negative_prompt", ""))
    transparent_background = bool(pack.get("transparent_background", True))
    seeds = [int(v) for v in pack.get("seeds", [101])]
    scenarios = list(pack.get("scenarios", []))
    if not scenarios:
        raise SystemExit("No scenarios in pack")

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    run_log: dict[str, object] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "pack": args.pack,
        },
        "runs": [],
    }

    failures = 0
    total = 0
    for scenario in scenarios:
        scenario_id = str(scenario.get("id", "")).strip()
        prompt = str(scenario.get("prompt", "")).strip()
        size = str(scenario.get("size", "1024x1024"))
        steps = int(scenario.get("steps", 28))
        cfg = float(scenario.get("cfg", 5.0))
        if not scenario_id or not prompt:
            print(f"Skipping invalid scenario: {scenario}", file=sys.stderr)
            continue
        safe_id = _safe(scenario_id)
        for seed in seeds:
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
            target = out_root / safe_id / f"{safe_id}__s{seed}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                png, job_id = _post_binary(args.base_url, args.api_key, payload, args.timeout_s)
                target.write_bytes(png)
                print(f"[OK] {scenario_id} seed={seed} -> {target.as_posix()}")
                run_log["runs"].append(
                    {
                        "scenario_id": scenario_id,
                        "seed": seed,
                        "status": "ok",
                        "job_id": job_id,
                        "output_path": str(target.as_posix()),
                    }
                )
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if _should_retry_without_transparency(exc.code, body):
                    payload.pop("transparent_background", None)
                    try:
                        png, job_id = _post_binary(args.base_url, args.api_key, payload, args.timeout_s)
                        target.write_bytes(png)
                        print(
                            f"[OK] {scenario_id} seed={seed} -> {target.as_posix()} "
                            "(fallback: no transparent_background field)"
                        )
                        run_log["runs"].append(
                            {
                                "scenario_id": scenario_id,
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
                print(f"[FAIL] {scenario_id} seed={seed} status={exc.code} body={body}", file=sys.stderr)
                run_log["runs"].append(
                    {
                        "scenario_id": scenario_id,
                        "seed": seed,
                        "status": "http_error",
                        "http_status": exc.code,
                        "error_body": body,
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
