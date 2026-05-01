#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from statistics import mean

from PIL import Image, ImageChops, ImageFilter, ImageStat


def _hf_ratio(gray: Image.Image) -> float:
    """Estimate high-frequency detail ratio via downsample-upsample residual."""
    w, h = gray.size
    if w < 4 or h < 4:
        return 0.0
    small = gray.resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.BILINEAR)
    recon = small.resize((w, h), Image.Resampling.BILINEAR)
    residual = ImageChops.difference(gray, recon)
    residual_mean = ImageStat.Stat(residual).mean[0]
    gray_mean = ImageStat.Stat(gray).mean[0]
    denom = max(gray_mean, 1e-6)
    return float(residual_mean / denom)


def score_image(path: Path) -> dict[str, float | str]:
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        gray = rgba.convert("L")

        edge = gray.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edge)
        lum_stat = ImageStat.Stat(gray)
        alpha_stat = ImageStat.Stat(rgba.getchannel("A"))

        return {
            "file": str(path),
            "width": gray.size[0],
            "height": gray.size[1],
            "entropy": float(gray.entropy()),
            "luma_mean": float(lum_stat.mean[0]),
            "luma_stddev": float(lum_stat.stddev[0]),
            "edge_mean": float(edge_stat.mean[0]),
            "edge_stddev": float(edge_stat.stddev[0]),
            "hf_ratio": _hf_ratio(gray),
            "alpha_mean": float(alpha_stat.mean[0]),
            "alpha_coverage_>0": float(alpha_stat.mean[0] / 255.0),
            "file_size_kb": float(path.stat().st_size / 1024.0),
        }


def _collect(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(sorted(Path(".").glob(pat)))
    return [p for p in out if p.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute no-reference image quality metrics for PNG/JPG files."
    )
    parser.add_argument(
        "patterns",
        nargs="+",
        help='Glob patterns, e.g. "outputs/tree-review/flux1-vae-ab-v2/*.png"',
    )
    parser.add_argument("--csv-out", default="", help="Optional CSV output path")
    args = parser.parse_args()

    files = _collect(args.patterns)
    if not files:
        raise SystemExit("No files matched the provided pattern(s).")

    rows = [score_image(p) for p in files]
    rows.sort(key=lambda r: str(r["file"]))

    headers = list(rows[0].keys())
    if args.csv_out:
        out_path = Path(args.csv_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    for row in rows:
        print(
            f'{row["file"]} | entropy={row["entropy"]:.3f} | '
            f'edge_std={row["edge_stddev"]:.3f} | hf_ratio={row["hf_ratio"]:.4f} | '
            f'alpha_cov={row["alpha_coverage_>0"]:.3f} | size_kb={row["file_size_kb"]:.1f}'
        )

    print("\nAverages:")
    print(f"entropy={mean(float(r['entropy']) for r in rows):.3f}")
    print(f"edge_stddev={mean(float(r['edge_stddev']) for r in rows):.3f}")
    print(f"hf_ratio={mean(float(r['hf_ratio']) for r in rows):.4f}")
    print(f"alpha_coverage_>0={mean(float(r['alpha_coverage_>0']) for r in rows):.3f}")
    print(f"file_size_kb={mean(float(r['file_size_kb']) for r in rows):.1f}")
    if args.csv_out:
        print(f"\nWrote CSV: {args.csv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
