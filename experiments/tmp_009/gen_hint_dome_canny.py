"""Generate a canny-style hint image for ControlNet base-plate experiment (iter 14).

A 1024×1024 black canvas with a single centered low-rounded-dome outline in
white (~2 px). Canny ControlNet expects edges drawn as white-on-black; the
xinsir union promax in canny mode will pull the SDXL base toward placing
foliage edges along this outline and suppressing detail outside.

Dimensions chosen to enforce "low rounded shrub mass" (wider than tall):
  width  ≈ 720 px  → ~70% of canvas
  height ≈ 520 px  → ~50% of canvas
  centred horizontally; bottom of dome at y≈860 (leaves ~164 px below;
  intentionally small to discourage model adding a ground patch).

Run:
  python experiments/tmp_009/gen_hint_dome_canny.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "experiments" / "tmp_009" / "hint_dome_canny_1024.png"

CANVAS = 1024
DOME_W = 720
DOME_H = 520
EDGE_WIDTH = 2          # canny convention: thin edges
CENTER_X = CANVAS // 2  # 512
CENTER_Y = 600          # slightly below middle


def main() -> None:
    img = Image.new("RGB", (CANVAS, CANVAS), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    left = CENTER_X - DOME_W // 2     # 152
    right = CENTER_X + DOME_W // 2    # 872
    top = CENTER_Y - DOME_H // 2      # 340
    bottom = CENTER_Y + DOME_H // 2   # 860
    draw.ellipse((left, top, right, bottom), outline=(255, 255, 255), width=EDGE_WIDTH)
    img.save(OUT)
    print(f"wrote: {OUT}")
    print(f"dome bbox: ({left}, {top}) -> ({right}, {bottom})")
    print(f"empty above: {top} px · empty below: {CANVAS - bottom} px")


if __name__ == "__main__":
    main()
