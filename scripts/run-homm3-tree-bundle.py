#!/usr/bin/env python
"""Run all HoMM3 biome bundle lanes plus tree-environment batch under one bundle root.

Live run expects ~2445 PNG requests total with current packs (pytest-stable HoMM matrix:
terrain 462 + structures 948 + misc 360 + bush 270 + mushroom 270 = 2310;
tree batch 15 environments x 3 species x 3 seeds = 135). Exact totals drift if packs change.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

def _first_api_key_from_env() -> str:
    """Same comma-separated list as docker-compose `API_KEYS`; use first key for CLI batches."""
    raw = os.environ.get("API_KEYS", "").strip()
    if not raw:
        return ""
    return raw.split(",")[0].strip()


_HOMM3_PACKS: tuple[tuple[str, str], ...] = (
    ("terrain", "docs/architecture/homm3-flux-terrain-biome-pack.json"),
    ("structures", "docs/architecture/homm3-flux-structure-biome-pack.json"),
    ("misc", "docs/architecture/homm3-flux-misc-biome-pack.json"),
    ("bush", "docs/architecture/homm3-flux-bush-biome-pack.json"),
    ("mushroom", "docs/architecture/homm3-flux-mushroom-biome-pack.json"),
)


def _run_step(
    *,
    argv: list[str],
    label: str,
) -> int:
    print(f"\n=== {label} ===", flush=True)
    proc = subprocess.run(argv, cwd=_REPO_ROOT)
    code = int(proc.returncode)
    if code != 0:
        print(f"[FAIL] {label} exit={code}", file=sys.stderr, flush=True)
    else:
        print(f"[OK] {label}", flush=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot orchestrator: five HoMM3 biome-bundle lanes (separate output "
            "subfolders so manifests/logs do not overwrite), then tree-environment batch."
        ),
    )
    parser.add_argument(
        "--bundle-root",
        default="outputs/homm3-bundle/pass-full-001",
        help="Root folder; HoMM lanes -> <root>/homm3/<lane>/, trees -> <root>/trees/.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8700")
    parser.add_argument(
        "--api-key",
        default="",
        help=(
            "Generation API key. If omitted, uses the first entry in env API_KEYS "
            "(same variable as docker-compose). Required for live runs unless API_KEYS is set."
        ),
    )
    parser.add_argument(
        "--tree-pack",
        default="docs/architecture/tree-environment-batch-pack.json",
    )
    parser.add_argument("--timeout-s", type=float, default=360.0)
    parser.add_argument("--model-override", default="", help="Forwarded to HoMM lanes only.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-homm3", action="store_true")
    parser.add_argument("--skip-trees", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Pass through to HoMM and tree batches: skip generations whose PNG already exists "
            "(non-empty). Preserves each lane's asset-manifest.ndjson when resuming HoMM."
        ),
    )
    args = parser.parse_args()

    bundle_root = (_REPO_ROOT / args.bundle_root).resolve()
    bundle_root.mkdir(parents=True, exist_ok=True)

    api_key = str(args.api_key).strip() or _first_api_key_from_env()
    if not args.dry_run and not api_key:
        parser.error(
            "No API key: pass --api-key or set API_KEYS in the environment "
            "(e.g. from your .env used by docker-compose)."
        )
    tree_api_key = api_key if api_key else "dummy"

    if not args.dry_run:
        print(
            "Planned generations (~2445 PNGs at default packs): "
            "HoMM lanes 2310 + trees 135 — server must stay up at --base-url.\n",
            flush=True,
        )

    py = sys.executable
    homm3_script = _REPO_ROOT / "scripts" / "homm3-biome-bundle-batch.py"
    tree_script = _REPO_ROOT / "scripts" / "tree-environment-batch.py"

    steps_log: list[dict[str, object]] = []
    worst = 0

    def record(label: str, code: int, extra: dict[str, object] | None = None) -> None:
        row: dict[str, object] = {"label": label, "exit_code": code}
        if extra:
            row.update(extra)
        steps_log.append(row)

    if not args.skip_homm3:
        for lane_key, pack_rel in _HOMM3_PACKS:
            pack_path = _REPO_ROOT / pack_rel
            if not pack_path.is_file():
                print(f"[FAIL] missing pack {pack_path}", file=sys.stderr, flush=True)
                record(f"homm3:{lane_key}", 1, {"pack": str(pack_rel)})
                worst = max(worst, 1)
                continue

            out_dir = bundle_root / "homm3" / lane_key
            argv = [
                py,
                str(homm3_script),
                "--pack",
                str(pack_path.relative_to(_REPO_ROOT)),
                "--base-url",
                args.base_url,
                "--out-dir",
                str(out_dir.relative_to(_REPO_ROOT)),
                "--timeout-s",
                str(args.timeout_s),
            ]
            if args.model_override.strip():
                argv.extend(["--model-override", args.model_override.strip()])
            if args.dry_run:
                argv.append("--dry-run")
            else:
                argv.extend(["--api-key", api_key])
            if args.resume:
                argv.append("--resume")

            code = _run_step(argv=argv, label=f"HoMM3 lane={lane_key}")
            worst = max(worst, code)
            record(f"homm3:{lane_key}", code, {"out_dir": str(out_dir)})
    else:
        print("Skipping all HoMM3 lanes (--skip-homm3).", flush=True)

    if not args.skip_trees:
        tree_out = bundle_root / "trees"
        tree_argv = [
            py,
            str(tree_script),
            "--pack",
            args.tree_pack,
            "--base-url",
            args.base_url,
            "--api-key",
            tree_api_key,
            "--out-dir",
            str(tree_out.relative_to(_REPO_ROOT)),
            "--timeout-s",
            str(args.timeout_s),
        ]
        if args.dry_run:
            tree_argv.append("--dry-run")
        if args.resume:
            tree_argv.append("--resume")

        code = _run_step(argv=tree_argv, label="Tree environment batch")
        worst = max(worst, code)
        record("trees", code, {"out_dir": str(tree_out)})
    else:
        print("Skipping tree batch (--skip-trees).", flush=True)

    summary_path = bundle_root / "bundle-orchestrator-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "bundle_root": str(bundle_root.as_posix()),
                "dry_run": args.dry_run,
                "resume": args.resume,
                "base_url": args.base_url,
                "steps": steps_log,
                "worst_exit_code": worst,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nOrchestrator summary: {summary_path.as_posix()}", flush=True)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
