"""PoC — NAG biome-generalisation honesty check (TMP_009 debt #4).

3 biomes × 3 seeds = 9-cell grid of the same entry under NAG. If NAG generalises,
the grid should look uniformly clean. If it's biome-specific, this grid will show
where it breaks.

Run after the strict pack regen with all 3 biomes.
Usage:  python poc_nag_biome_grid.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent.parent  # experiments/tmp_009 → repo root
BASE = REPO / "outputs" / "spike-strict-prompt"
OUT = REPO / "outputs" / "spike-strict-prompt" / "biome_grid.png"

ENTRY = "alpine_dwarf_shrub_cluster"
BIOMES = ("abyss_chaos_rift", "grassland_temperate", "snow_frost")
SEEDS = (101, 202, 303)
DOWNSCALE = 0.32


def load(p: Path) -> Image.Image:
    img = Image.open(p).convert("RGBA")
    w, h = img.size
    return img.resize((int(w * DOWNSCALE), int(h * DOWNSCALE)), Image.LANCZOS)


def main() -> None:
    pad = 12
    label_h = 24
    cell_w = cell_h = int(1024 * DOWNSCALE)

    rows: list[tuple[str, list[Image.Image]]] = []
    for biome in BIOMES:
        imgs: list[Image.Image] = []
        for s in SEEDS:
            p = BASE / biome / "bush" / f"{ENTRY}__1024_1024__s{s}.png"
            if p.exists():
                imgs.append(load(p))
            else:
                imgs.append(Image.new("RGBA", (cell_w, cell_h), (200, 0, 0, 64)))
        rows.append((biome, imgs))

    n_cols = len(SEEDS)
    grid_w = n_cols * cell_w + (n_cols + 1) * pad + 160
    grid_h = len(rows) * (cell_h + label_h + pad) + pad
    grid = Image.new("RGBA", (grid_w, grid_h), (240, 240, 240, 255))
    d = ImageDraw.Draw(grid)

    d.text((pad, pad // 2), "biome / seed:", fill=(0, 0, 0, 255))
    for i, s in enumerate(SEEDS):
        x = 160 + pad + i * (cell_w + pad)
        d.text((x + cell_w // 2 - 20, pad // 2), f"s{s}", fill=(0, 0, 0, 255))

    y = label_h
    for biome, imgs in rows:
        d.text((pad, y + cell_h // 2 - 8), biome, fill=(0, 0, 0, 255))
        for i, im in enumerate(imgs):
            x = 160 + pad + i * (cell_w + pad)
            grid.paste(im, (x, y), im)
        y += cell_h + label_h + pad

    grid.save(OUT)
    print(f"wrote: {OUT}")


if __name__ == "__main__":
    main()
