from __future__ import annotations

from io import BytesIO

from PIL import Image


def apply_transparent_background(png_bytes: bytes, *, threshold: int = 245) -> bytes:
    """Convert near-white background pixels to transparent alpha."""
    with Image.open(BytesIO(png_bytes)) as img:
        rgba = img.convert("RGBA")
        pixels = rgba.load()
        width, height = rgba.size
        for x in range(width):
            for y in range(height):
                r, g, b, a = pixels[x, y]
                if r >= threshold and g >= threshold and b >= threshold:
                    pixels[x, y] = (r, g, b, 0)
        out = BytesIO()
        rgba.save(out, format="PNG")
        return out.getvalue()
