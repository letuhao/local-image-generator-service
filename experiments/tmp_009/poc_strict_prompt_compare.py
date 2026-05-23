"""PoC — A/B compare of original vs strict-prompt regen (TMP_009 spike).

Produces a 2-row grid:
  TOP    = outputs/homm3-bundle/.../alpine_dwarf_shrub_cluster s101/s202/s303
           (the original "lộn xộn" set with baked-in rocks/grass/iso platforms)
  BOTTOM = outputs/spike-strict-prompt/.../alpine_dwarf_shrub_cluster s101/s202/s303
           (regenerated with `homm3-flux-bush-strict-spike-pack.json` — hard
            negative prompt forbidding any ground/base/platform/terrain)

Same entry, same seeds, same model/LoRA — only the prompt+negative changed.
If the strict row is clean (no rocks, no iso platforms, props float on white)
the gen-side fix is validated and the next step is rolling the strict template
out to the full bundle.

Run AFTER the strict-prompt regen batch has produced files in
`outputs/spike-strict-prompt/`.

Usage:  python poc_strict_prompt_compare.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent.parent  # experiments/tmp_009 → repo root
ORIGINAL_DIR = (
    REPO
    / "outputs"
    / "homm3-bundle"
    / "pass-full-001"
    / "homm3"
    / "bush"
    / "abyss_chaos_rift"
    / "bush"
)
# The strict batch's out-dir layout mirrors the homm3 bundle:
#   <out-dir>/<biome>/<lane>/<entry>__<size>__s<seed>.png
STRICT_DIR = (
    REPO
    / "outputs"
    / "spike-strict-prompt"
    / "abyss_chaos_rift"
    / "bush"
)
OUT = REPO / "outputs" / "spike-strict-prompt"
OUT.mkdir(parents=True, exist_ok=True)

ENTRY = "alpine_dwarf_shrub_cluster"
SEEDS = (101, 202, 303)
CELL_DOWNSCALE = 0.4  # source images are 1024x1024; downscale for the strip


def load_row(src_dir: Path) -> list[Image.Image]:
    rows: list[Image.Image] = []
    for seed in SEEDS:
        p = src_dir / f"{ENTRY}__1024_1024__s{seed}.png"
        if not p.exists():
            print(f"missing: {p}")
            continue
        img = Image.open(p).convert("RGBA")
        w, h = img.size
        img = img.resize((int(w * CELL_DOWNSCALE), int(h * CELL_DOWNSCALE)), Image.LANCZOS)
        rows.append(img)
    return rows


def label(text: str, w: int, h: int = 28) -> Image.Image:
    img = Image.new("RGBA", (w, h), (220, 220, 220, 255))
    d = ImageDraw.Draw(img)
    d.text((8, 6), text, fill=(0, 0, 0, 255))
    return img


def make_grid(top: list[Image.Image], bottom: list[Image.Image]) -> Image.Image:
    pad = 12
    cell_w = max(i.width for i in top + bottom)
    cell_h = max(i.height for i in top + bottom)
    n = max(len(top), len(bottom))
    if n == 0:
        raise SystemExit("no images to compare")
    grid_w = n * cell_w + (n + 1) * pad
    label_h = 28
    grid_h = 2 * cell_h + 4 * pad + 2 * label_h
    grid = Image.new("RGBA", (grid_w, grid_h), (240, 240, 240, 255))

    grid.paste(label("ORIGINAL (homm3-bundle, soft negative)", grid_w - 2 * pad, label_h),
               (pad, pad))
    for i, im in enumerate(top):
        x = pad + i * (cell_w + pad) + (cell_w - im.width) // 2
        y = 2 * pad + label_h + (cell_h - im.height) // 2
        grid.paste(im, (x, y), im)

    y2 = 2 * pad + label_h + cell_h + pad
    grid.paste(label("STRICT PROMPT (no-ground negative, gen-side fix)",
                     grid_w - 2 * pad, label_h),
               (pad, y2))
    for i, im in enumerate(bottom):
        x = pad + i * (cell_w + pad) + (cell_w - im.width) // 2
        y = y2 + label_h + pad + (cell_h - im.height) // 2
        grid.paste(im, (x, y), im)

    return grid


def main() -> None:
    if not ORIGINAL_DIR.exists():
        raise SystemExit(f"original dir not found: {ORIGINAL_DIR}")
    if not STRICT_DIR.exists():
        raise SystemExit(
            f"strict dir not found: {STRICT_DIR}\n"
            "Run the strict-prompt batch first:\n"
            "  .venv/Scripts/python.exe scripts/homm3-biome-bundle-batch.py \\\n"
            "    --pack docs/architecture/homm3-flux-bush-strict-spike-pack.json \\\n"
            "    --base-url http://127.0.0.1:8700 --api-key test-gen-key \\\n"
            "    --out-dir outputs/spike-strict-prompt"
        )

    top = load_row(ORIGINAL_DIR)
    bottom = load_row(STRICT_DIR)
    if not top or not bottom:
        raise SystemExit("missing seed files — see prints above")

    grid = make_grid(top, bottom)
    out_path = OUT / f"{ENTRY}_original_vs_strict.png"
    grid.save(out_path)
    print(f"wrote: {out_path}")


if __name__ == "__main__":
    main()
