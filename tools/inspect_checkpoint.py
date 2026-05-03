#!/usr/bin/env python3
"""Inspect large Safetensors checkpoints without loading tensor data into memory.

Reads only the Safetensors JSON header (layout: u64 little-endian length + UTF-8 JSON)
and aggregates per-tensor storage, dtypes, and rough component buckets from key names.
Use this before choosing a downsizing path (GGUF split, fp8, pruned build, etc.).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TensorEntry:
    name: str
    dtype: str
    shape: tuple[int, ...]
    nbytes: int


def read_safetensors_index(path: Path) -> tuple[dict[str, str] | None, list[TensorEntry]]:
    """Return optional __metadata__ dict and a list of tensor index entries (no raw weights)."""
    with path.open("rb") as f:
        hlen_bytes = f.read(8)
        if len(hlen_bytes) < 8:
            msg = f"{path}: file too small for safetensors header"
            raise ValueError(msg)
        (header_len,) = struct.unpack("<Q", hlen_bytes)
        if header_len > 256 * 1024 * 1024:  # 256 MiB cap — corrupt or malicious
            msg = f"{path}: implausible header length {header_len}"
            raise ValueError(msg)
        header_buf = f.read(header_len)
        if len(header_buf) < header_len:
            msg = f"{path}: truncated header (expected {header_len} bytes)"
            raise ValueError(msg)

    header: dict = json.loads(header_buf.decode("utf-8"))
    metadata: dict[str, str] | None = None
    raw_meta = header.get("__metadata__")
    if isinstance(raw_meta, dict):
        metadata = {str(k): str(v) for k, v in raw_meta.items()}

    entries: list[TensorEntry] = []
    for name, spec in header.items():
        if name == "__metadata__":
            continue
        if not isinstance(spec, dict):
            continue
        dtype = str(spec.get("dtype", "unknown"))
        shape = tuple(int(x) for x in spec.get("shape", ()))
        offsets = spec.get("data_offsets")
        if not isinstance(offsets, list) or len(offsets) != 2:
            continue
        start, end = int(offsets[0]), int(offsets[1])
        nbytes = max(0, end - start)
        entries.append(TensorEntry(name=name, dtype=dtype, shape=shape, nbytes=nbytes))

    return metadata, entries


def bucket_tensor(name: str) -> str:
    """Coarse component guess from tensor key (for bundled / AIO checkpoints)."""
    lower = name.lower()
    if "first_stage" in lower or ("decoder" in lower and "vae" in lower):
        return "vae-ish"
    if "vae" in lower and "loss" not in lower:
        return "vae-ish"
    if any(x in lower for x in ("cond_stage", "text_enc", "text_encoder", "te.", ".te.")):
        return "text/cond"
    if any(
        x in lower
        for x in (
            "clip",
            "open_clip",
            "openclip",
            "t5xxl",
            "t5_model",
            "transformer.resblocks",
        )
    ):
        return "text/cond"
    if "qwen" in lower or "vl_" in lower or ("vision" in lower and "model" in lower):
        return "text/cond"
    if "diffusion" in lower or "unet" in lower or "model.diffusion" in lower:
        return "diffusion"
    if lower.startswith("model.") and "diffusion" not in lower:
        return "other/unclassified"
    return "other/unclassified"


def format_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if x < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(x)} {unit}"
            return f"{x:.2f} {unit}"
        x /= 1024
    return f"{x:.2f} TiB"


def report_inspect(path: Path, *, top_n: int, as_json: bool) -> int:
    if path.suffix.lower() not in {".safetensors", ".sft", ".mdl"}:
        print(
            f"Warning: expected a Safetensors file (.safetensors); got {path.suffix!r}.",
            file=sys.stderr,
        )

    meta, entries = read_safetensors_index(path)
    disk = path.stat().st_size
    total_payload = sum(e.nbytes for e in entries)
    by_dtype = Counter(e.dtype for e in entries)
    by_bucket: dict[str, int] = defaultdict(int)
    for e in entries:
        by_bucket[bucket_tensor(e.name)] += e.nbytes

    largest = sorted(entries, key=lambda e: e.nbytes, reverse=True)[:top_n]

    if as_json:
        out = {
            "path": str(path.resolve()),
            "disk_bytes": disk,
            "tensor_count": len(entries),
            "payload_bytes_sum": total_payload,
            "metadata": meta,
            "by_dtype": dict(by_dtype),
            "by_bucket_bytes": dict(by_bucket),
            "largest_tensors": [
                {"name": e.name, "dtype": e.dtype, "shape": list(e.shape), "nbytes": e.nbytes}
                for e in largest
            ],
        }
        print(json.dumps(out, indent=2))
        return 0

    print(f"Path:           {path.resolve()}")
    print(f"On-disk size:   {format_bytes(disk)}")
    print(f"Tensors:        {len(entries)}")
    print(f"Payload (sum):  {format_bytes(total_payload)}  (from header data_offsets)")
    if meta:
        print("Metadata:")
        for k, v in sorted(meta.items())[:20]:
            print(f"  {k}: {v[:200]}{'…' if len(v) > 200 else ''}")
        if len(meta) > 20:
            print(f"  … ({len(meta) - 20} more keys)")
    print("\nStorage by coarse bucket (name heuristics):")
    for k in sorted(by_bucket, key=lambda x: -by_bucket[x]):
        print(f"  {k:22} {format_bytes(by_bucket[k])}")
    print("\nDtypes:")
    for dt, _ in by_dtype.most_common():
        print(f"  {dt:12} {by_dtype[dt]} tensors")
    print(f"\nTop {top_n} tensors by payload size:")
    for e in largest:
        short = e.name if len(e.name) <= 90 else e.name[:87] + "..."
        print(f"  {format_bytes(e.nbytes):>12}  {e.dtype:8}  {short}")
    return 0


def report_hints() -> int:
    print(
        """\
Downsizing large diffusion checkpoints - practical notes
========================================================

Bundled / "AIO" Safetensors (UNet + text + VAE in one file)
  - This tool only INSPECTS headers; it does not rewrite models.
  - Shrinking usually means switching FORMAT or DISTRIBUTION, not "compress one file":
      - Community GGUF builds for Qwen-Image / Qwen-Image-Edit (diffusion GGUF + separate
        text encoder + VAE). Search Hugging Face for "Qwen Image GGUF" / ComfyUI-GGUF.
      - Author "pruned" / fp8 variants if the publisher ships them.
  - Converting an arbitrary custom AIO anime merge to GGUF yourself is not a supported
    one-click path; weights must match the graph your runtime expects.

General levers
  - Use a smaller published variant (fp8, pruned, Q4/Q6 GGUF) from the same model family.
  - Keep production weights on disk symlink / NAS; service registry points at paths.
  - For this repo: add a new models.yaml entry + workflow only after you have a runnable
    stack in ComfyUI (loaders for GGUF vs single checkpoint differ).

Re-run inspection after replacing a file:
  uv run python tools/inspect_checkpoint.py inspect path/to/model.safetensors
"""
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Safetensors checkpoints (header-only, safe for huge files)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="Print size breakdown for .safetensors")
    p_ins.add_argument("paths", type=Path, nargs="+", help="One or more .safetensors paths")
    p_ins.add_argument("--top", type=int, default=15, help="How many largest tensors to list")
    p_ins.add_argument("--json", action="store_true", help="Machine-readable JSON")
    sub.add_parser("hints", help="Static notes on downsizing / GGUF / AIO")

    args = parser.parse_args(argv)
    if args.cmd == "hints":
        return report_hints()
    if args.cmd == "inspect":
        if args.json and len(args.paths) != 1:
            parser.error("--json supports exactly one checkpoint path")
        rc = 0
        for p in args.paths:
            if not p.is_file():
                print(f"Not a file: {p}", file=sys.stderr)
                rc = 1
                continue
            if len(args.paths) > 1 and not args.json:
                print(f"\n{'=' * 72}\n# {p}\n{'=' * 72}\n")
            report_inspect(p, top_n=args.top, as_json=args.json)
        return rc
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
