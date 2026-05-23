"""Generate a depth-map hint for iter 16 ControlNet depth-mode experiment.

Iter 15 (canny arc) gained the first V=2 clean output but multi-pom bouquet
and substrate-disc failure modes persisted on 6/9. Per architectural analysis:
canny mode is edge-control (model fills inside freely); depth mode is volume-
control (model places object volume at the depth-map location).

Hint format: same dome geometry as iter 14/15 (720×520 centred at 512,600),
but FILLED solid white on black bg. Standard depth-map convention is
white=near foreground, black=far background. A flat-white solid mass tells
the model "object volume occupies this region at uniform near depth".

For simplicity start with uniform white fill (no gradient). If model still
struggles with foreground integration, iter 17 could use a gradient depth
(white at dome centre fading to grey at edges = bulged-out 3D object).

Run:
  python experiments/tmp_009/gen_hint_dome_depth.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "experiments" / "tmp_009" / "hint_dome_depth_1024.png"

CANVAS = 1024
DOME_W = 720
DOME_H = 520
CENTER_X = CANVAS // 2  # 512
CENTER_Y = 600


def main() -> None:
    img = Image.new("RGB", (CANVAS, CANVAS), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    left = CENTER_X - DOME_W // 2
    right = CENTER_X + DOME_W // 2
    top = CENTER_Y - DOME_H // 2
    bottom = CENTER_Y + DOME_H // 2
    # Filled white dome. Depth convention: white = near, black = far. So this
    # tells the depth ControlNet "uniform near-foreground volume occupies the
    # dome region; everything else is far background".
    draw.ellipse((left, top, right, bottom), fill=(255, 255, 255))
    img.save(OUT)
    print(f"wrote: {OUT}")
    print(f"dome volume bbox: ({left}, {top}) -> ({right}, {bottom})")
    print(f"fill: uniform white (near-foreground depth)")


if __name__ == "__main__":
    main()
