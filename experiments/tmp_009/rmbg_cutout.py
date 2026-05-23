"""BRIA RMBG-1.4 cutout — TMP_009 DEBT #8 (ONNX path).

The white-threshold strip in `poc_static_consistency.py` only kills pure-white
pixels, which fails the moment Flux bakes a coloured iso platform / grass tuft
under the prop (debt #4 finding on light biomes). This module loads the local
BriaRMBG-1.4 ONNX model and returns a clean RGBA cutout where the alpha mask
reflects the model's "subject vs background" judgment, not a colour threshold.

ONNX was chosen over the PyTorch path because the bundled `briarmbg.py` (HF
auto_map class) is incompatible with transformers ≥5.7 — it lacks the new
`all_tied_weights_keys` attribute. The ONNX export sidesteps the class entirely.

Run:  python rmbg_cutout.py <in.png> <out.png>
or use `apply_rmbg(in_path)` from another script (e.g. updated composite spike).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

REPO = Path(__file__).resolve().parent.parent.parent
MODEL_PATH = REPO / "models" / "rmbg" / "bria-rmbg-1.4" / "onnx" / "model.onnx"
MODEL_INPUT_SIZE = (1024, 1024)

_session: ort.InferenceSession | None = None


def get_session() -> ort.InferenceSession:
    global _session
    if _session is None:
        providers = ort.get_available_providers()
        # Prefer CUDA if available; otherwise CPU. RMBG-1.4 on CPU is ~1-3 s/image
        # at 1024×1024 — still ~10× faster than the prior Python pixel loop.
        prefer = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in providers]
        _session = ort.InferenceSession(str(MODEL_PATH), providers=prefer)
    return _session


def _preprocess(im_pil: Image.Image) -> np.ndarray:
    """Mirror `models/rmbg/bria-rmbg-1.4/utilities.py::preprocess_image`.

    The PyTorch utility resizes to 1024×1024 with bilinear interpolation, casts to
    uint8, divides by 255, then normalises with mean=[0.5]*3 std=[1.0]*3 (so the
    net result is a per-channel subtract of 0.5). ONNX expects NCHW float32.
    """
    resized = im_pil.convert("RGB").resize(MODEL_INPUT_SIZE, Image.BILINEAR)
    arr = np.asarray(resized, dtype=np.float32) / 255.0
    arr = arr - 0.5
    arr = arr.transpose(2, 0, 1)[None, ...]  # HWC → NCHW
    return arr


def _postprocess(mask_chw: np.ndarray, orig_size: tuple[int, int]) -> np.ndarray:
    """Min-max normalise the mask to 0..255 uint8 and resize back to original size."""
    mask = mask_chw.squeeze()  # 1024×1024
    mn, mx = float(mask.min()), float(mask.max())
    if mx - mn < 1e-6:
        return np.zeros(orig_size[::-1], dtype=np.uint8)  # H, W
    mask = (mask - mn) / (mx - mn)
    mask_uint8 = (mask * 255).astype(np.uint8)
    mask_pil = Image.fromarray(mask_uint8, mode="L").resize(orig_size, Image.BILINEAR)
    return np.asarray(mask_pil)


def apply_rmbg(src_path: Path) -> Image.Image:
    """Return an RGBA Image with the RMBG-1.4 alpha mask applied."""
    session = get_session()
    pil_rgb = Image.open(src_path).convert("RGB")
    inp = _preprocess(pil_rgb)
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: inp})
    # BriaRMBG returns a tuple of supervision maps; the first is the main mask.
    main = outputs[0] if outputs[0].ndim >= 3 else outputs
    mask = _postprocess(main, pil_rgb.size)
    rgba = pil_rgb.convert("RGBA")
    rgba.putalpha(Image.fromarray(mask, mode="L"))
    return rgba


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: rmbg_cutout.py <in.png> <out.png>")
        sys.exit(1)
    in_p, out_p = Path(sys.argv[1]), Path(sys.argv[2])
    rgba = apply_rmbg(in_p)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(out_p)
    print(f"wrote: {out_p}")


if __name__ == "__main__":
    main()
