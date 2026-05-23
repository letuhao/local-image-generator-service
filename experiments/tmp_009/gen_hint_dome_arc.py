"""Generate an open-top dome arc hint for iter 15.

Iter 14 closed-ellipse outline read as ceramic bowl/saucer rim (3/9 explicit
dishes per sub-agent). Sub-agent prescribed: open-top arc (no bottom closure)
to break the container reading — the arc represents the TOP of a foliage
cluster, not the rim of a vessel.

Geometry: same outer bounds as iter 14 dome (720×520, centred at 512,600)
but draw ONLY the upper 180° arc (top semicircle). No bottom line at all.
Empty above arc, empty below where the rim used to be.

Run:
  python experiments/tmp_009/gen_hint_dome_arc.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "experiments" / "tmp_009" / "hint_dome_arc_1024.png"

CANVAS = 1024
DOME_W = 720
DOME_H = 520
EDGE_WIDTH = 2
CENTER_X = CANVAS // 2  # 512
CENTER_Y = 600


def main() -> None:
    img = Image.new("RGB", (CANVAS, CANVAS), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    left = CENTER_X - DOME_W // 2     # 152
    right = CENTER_X + DOME_W // 2    # 872
    top = CENTER_Y - DOME_H // 2      # 340
    bottom = CENTER_Y + DOME_H // 2   # 860
    # PIL arc angles: 0° = east (right), increases clockwise.
    # Top semicircle = 180° (west / left) to 360°/0° (east / right) going via 270° (north / top).
    # Equivalent expression: start=180, end=360.
    draw.arc((left, top, right, bottom), start=180, end=360, fill=(255, 255, 255), width=EDGE_WIDTH)
    img.save(OUT)
    print(f"wrote: {OUT}")
    print(f"arc bbox: ({left}, {top}) -> ({right}, {bottom})")
    print(f"arc span: top semicircle only (no bottom closure)")


if __name__ == "__main__":
    main()
