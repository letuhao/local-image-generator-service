"""PoC — static-prop consistency spike (TMP_009 §7, design repo).

Goal: prove the "lộn xộn" can be killed for STATIC iso props with **no GPU, no
mesh, no Blender, no new ControlNet** — purely by:

  1. strip near-white background (matches `app/postprocess/transparency.py`)
  2. tight-crop to opaque bbox
  3. normalize HEIGHT to one target → kills per-seed scale drift
  4. composite onto ONE canonical iso diamond base-plate

If the 3 seeds (s101/s202/s303) of the same entry sit on the same tile at the
same scale, the static path is validated and the next step is wiring a batch
run for all biomes. If not, escalate to ControlNet-depth or multi-view.

Run:  python poc_static_consistency.py
Output: outputs/spike-static-consistency/
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent.parent  # experiments/tmp_009 → repo root
DEFAULT_BUNDLE = (
    REPO
    / "outputs"
    / "homm3-bundle"
    / "pass-full-001"
    / "homm3"
    / "bush"
    / "abyss_chaos_rift"
    / "bush"
)
DEFAULT_OUT = REPO / "outputs" / "spike-static-consistency"

# Canonical iso tile — TMP_009 §2 (2:1 dimetric). Authoring 128x64; spike uses
# 256x128 (2x) so the before/after strip is legible without further zoom.
TILE_W, TILE_H = 256, 128
PROP_TARGET_H = 384       # ~3 tile heights → typical iso bush/tree silhouette
WHITE_THRESHOLD = 245     # matches transparency.py
BASE_TRIM_FRAC = 0.18     # drop bottom 18% of bbox — SD bakes ground/rocks here

ENTRY = "alpine_dwarf_shrub_cluster"
SEEDS = (101, 202, 303)


def make_base_plate(tile_w: int, tile_h: int) -> Image.Image:
    """One canonical 2:1 iso diamond. Apex order: top, right, bottom, left."""
    img = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = tile_w // 2, tile_h // 2
    diamond = [(cx, 0), (tile_w - 1, cy), (cx, tile_h - 1), (0, cy)]
    draw.polygon(diamond, fill=(110, 142, 80, 255), outline=(70, 90, 50, 255))
    return img


def strip_white_bg(img: Image.Image, threshold: int = WHITE_THRESHOLD) -> Image.Image:
    rgba = img.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    for x in range(w):
        for y in range(h):
            r, g, b, _a = px[x, y]
            if r >= threshold and g >= threshold and b >= threshold:
                px[x, y] = (r, g, b, 0)
    return rgba


def tight_crop(img: Image.Image) -> Image.Image:
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def trim_bottom(img: Image.Image, frac: float) -> Image.Image:
    """Drop the bottom `frac` of the image height — SD frequently bakes a ground
    plate / rocks / grass tuft here that fights the canonical tile."""
    if frac <= 0:
        return img
    w, h = img.size
    cut = int(h * (1.0 - frac))
    return img.crop((0, 0, w, max(1, cut)))


def normalize_height(img: Image.Image, target_h: int) -> Image.Image:
    w, h = img.size
    if h == 0:
        return img
    scale = target_h / h
    return img.resize((max(1, int(w * scale)), target_h), Image.LANCZOS)


def composite_on_tile(
    prop: Image.Image,
    base_plate: Image.Image,
    tile_w: int,
    tile_h: int,
) -> Image.Image:
    """Place `prop` so its bottom-center sits at the diamond's visual center
    (cx, cy of the tile) — i.e. the prop stands on the diamond's floor."""
    canvas_w = max(prop.width, tile_w) + 64
    canvas_h = prop.height + tile_h + 32
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    tile_x = (canvas_w - tile_w) // 2
    tile_y = canvas_h - tile_h
    canvas.paste(base_plate, (tile_x, tile_y), base_plate)

    diamond_cx = tile_x + tile_w // 2
    diamond_cy = tile_y + tile_h // 2
    prop_x = diamond_cx - prop.width // 2
    prop_y = diamond_cy - prop.height
    canvas.paste(prop, (prop_x, prop_y), prop)
    return canvas


def process_one(src_path: Path, base_plate: Image.Image, use_rmbg: bool) -> Image.Image:
    if use_rmbg:
        # DEBT #8: BRIA RMBG-1.4 ONNX cutout — semantic foreground/background
        # mask, ~1 s/image on CPU, far cleaner edges than `strip_white_bg` and
        # no white-leak on near-white prop highlights. Does NOT semantically
        # separate "prop only" from "prop on a depicted iso platform" — that
        # case is a separate SAM2-text-prompted spike, not this debt.
        from rmbg_cutout import apply_rmbg

        cut = apply_rmbg(src_path)
    else:
        cut = strip_white_bg(Image.open(src_path))
    cropped = tight_crop(cut)
    trimmed = trim_bottom(cropped, BASE_TRIM_FRAC)
    sized = normalize_height(trimmed, PROP_TARGET_H)
    return composite_on_tile(sized, base_plate, TILE_W, TILE_H)


def make_comparison_strip(
    before: list[Image.Image], after: list[Image.Image]
) -> Image.Image:
    """Two rows: top = originals (full SD output), bottom = normalized iso composites."""
    pad = 16
    cell_w = max(i.width for i in before + after)
    cell_h_b = max(i.height for i in before)
    cell_h_a = max(i.height for i in after)
    n = len(before)
    strip_w = n * cell_w + (n + 1) * pad
    strip_h = cell_h_b + cell_h_a + 3 * pad
    strip = Image.new("RGBA", (strip_w, strip_h), (240, 240, 240, 255))
    for i, im in enumerate(before):
        x = pad + i * (cell_w + pad) + (cell_w - im.width) // 2
        y = pad + (cell_h_b - im.height) // 2
        strip.paste(im, (x, y), im if im.mode == "RGBA" else None)
    for i, im in enumerate(after):
        x = pad + i * (cell_w + pad) + (cell_w - im.width) // 2
        y = 2 * pad + cell_h_b + (cell_h_a - im.height) // 2
        strip.paste(im, (x, y), im)
    return strip


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE,
                   help=f"directory holding {{ENTRY}}__1024_1024__s{{SEED}}.png inputs "
                        f"(default: {DEFAULT_BUNDLE.relative_to(REPO)})")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT,
                   help=f"output directory (default: {DEFAULT_OUT.relative_to(REPO)})")
    p.add_argument("--suffix", type=str, default="",
                   help="suffix appended to output filenames (e.g. '_nag')")
    p.add_argument("--no-rmbg", action="store_true",
                   help="use the legacy white-threshold strip instead of RMBG-1.4 (DEBT #8). "
                        "RMBG is the default; pass --no-rmbg to fall back.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    bundle: Path = args.bundle
    out: Path = args.out
    sfx: str = args.suffix
    use_rmbg: bool = not args.no_rmbg

    if not bundle.exists():
        raise SystemExit(f"bundle path not found: {bundle}")
    out.mkdir(parents=True, exist_ok=True)

    base = make_base_plate(TILE_W, TILE_H)
    base.save(out / "base_plate_canonical.png")

    print(f"background strip: {'RMBG-1.4 ONNX' if use_rmbg else 'white-threshold (legacy)'}")

    befores: list[Image.Image] = []
    afters: list[Image.Image] = []
    for seed in SEEDS:
        src = bundle / f"{ENTRY}__1024_1024__s{seed}.png"
        if not src.exists():
            print(f"skip: {src} not found")
            continue
        print(f"processing seed {seed} ...")
        befores.append(Image.open(src).convert("RGBA"))
        composed = process_one(src, base, use_rmbg)
        afters.append(composed)
        composed.save(out / f"{ENTRY}_s{seed}_iso{sfx}.png")

    if not befores:
        raise SystemExit("no source images found")

    strip = make_comparison_strip(befores, afters)
    out_path = out / f"{ENTRY}_before_after{sfx}.png"
    strip.save(out_path)
    print(f"\nwrote comparison strip: {out_path}")
    print(f"individual iso composites: {out}")
    print(f"base plate: {out / 'base_plate_canonical.png'}")


if __name__ == "__main__":
    main()
