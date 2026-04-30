#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download terrain review outputs into human-readable folders."
    )
    parser.add_argument(
        "--file",
        default="docs/architecture/terrain-model-tracking.json",
        help="Tracking JSON path.",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs/terrain-review/pass-001",
        help="Export root folder.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Bearer token for protected image URLs.",
    )
    parser.add_argument(
        "--candidate",
        default="",
        help="Optional candidate_id filter (export only matching runs).",
    )
    args = parser.parse_args()

    track_path = Path(args.file)
    out_root = Path(args.out_dir)
    data = json.loads(track_path.read_text(encoding="utf-8"))
    runs = data.get("runs", [])
    if not runs:
        print("No runs found to export.")
        return 0

    out_root.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0

    for run in runs:
        if args.candidate and run.get("candidate_id") != args.candidate:
            continue
        url = (run.get("output_path") or "").strip()
        candidate_id = _safe(str(run.get("candidate_id") or "unknown_candidate"))
        terrain = _safe(str(run.get("terrain_family") or "unknown_terrain"))
        seed = run.get("seed")
        job_hint = _safe(str(run.get("job_id") or "no_job"))
        if not url:
            skipped += 1
            continue

        target_dir = out_root / candidate_id / terrain
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"s{seed}__{job_hint}.png"
        target = target_dir / file_name
        if target.exists():
            run["local_output_path"] = str(target.as_posix())
            skipped += 1
            continue

        req = request.Request(url, method="GET")
        if args.api_key:
            req.add_header("Authorization", f"Bearer {args.api_key}")
        with request.urlopen(req, timeout=180) as resp:
            content = resp.read()
        target.write_bytes(content)
        run["local_output_path"] = str(target.as_posix())
        downloaded += 1

    data.setdefault("meta", {})["last_updated"] = datetime.now(timezone.utc).isoformat()
    track_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    index_path = out_root / "index.md"
    lines = ["# Terrain Review Export Index", ""]
    for run in runs:
        local_path = run.get("local_output_path")
        if not local_path:
            continue
        lines.append(
            f"- `{run.get('candidate_id')}` | `{run.get('terrain_family')}` | "
            f"`seed={run.get('seed')}` | `{local_path}`"
        )
    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Downloaded: {downloaded}")
    print(f"Skipped: {skipped}")
    print(f"Index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
